"""Durable command receipts. No model reasoning, credentials or file bodies."""
import json
import re
import time


def clean(value, limit=12000):
    text = str(value)
    text = re.sub(r'(?i)(bearer\s+|(?:api[_-]?key|token|password|authorization)["\s:=]+)[^\s,"}]+', r'\1[REDACTED]', text)
    text = re.sub(r'\b(?:AIza[\w-]{20,}|gsk_[\w-]{15,}|sk-[\w-]{15,})', '[REDACTED]', text)
    return text[:limit]


def safe_value(value, depth=0):
    if depth > 5:
        return '[bounded]'
    if isinstance(value, dict):
        return {str(k): ('[omitted]' if re.search(r'(?i)secret|password|api.?key|authorization|^content$|^memory$|^instructions$', str(k))
                         else safe_value(v, depth+1)) for k,v in list(value.items())[:40]}
    if isinstance(value, list):
        return [safe_value(v, depth+1) for v in value[:60]]
    if isinstance(value, str):
        return clean(value)
    return value if value is None or isinstance(value,(int,float,bool)) else clean(value)


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS task_commands(
        task_id TEXT NOT NULL, number INTEGER NOT NULL, agent TEXT NOT NULL,
        action TEXT NOT NULL, args TEXT NOT NULL, phase TEXT NOT NULL,
        started_at REAL NOT NULL, finished_at REAL, outcome TEXT,
        PRIMARY KEY(task_id,number))''')


def begin(state, task, action, args=None):
    with state._tx():
        schema(state.db)
        number=state.db.execute('SELECT COALESCE(MAX(number),0)+1 FROM task_commands WHERE task_id=?',(task['task_id'],)).fetchone()[0]
        state.db.execute('INSERT INTO task_commands VALUES(?,?,?,?,?,?,?,?,?)',
            (task['task_id'],number,task['agent'],action,json.dumps(safe_value(args or {})), 'running',time.time(),None,None))
        state._event(task['task_id'],'command_started',{'number':number,'action':action})
    return number


def end(state, task_id, number, outcome, failed=False):
    with state._tx():
        state.db.execute('UPDATE task_commands SET phase=?,finished_at=?,outcome=? WHERE task_id=? AND number=?',
            ('failed' if failed else 'completed',time.time(),json.dumps(safe_value(outcome),ensure_ascii=False),task_id,number))
        state._event(task_id,'command_finished',{'number':number,'failed':failed})

