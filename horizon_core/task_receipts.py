"""Read-only projection of actual state, command receipts and checked artifacts."""
import base64
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from .operations import clean, safe_value


def parse(raw,default=None):
    if isinstance(raw,(dict,list)):return raw
    try:return json.loads(raw) if raw else default
    except (TypeError,ValueError):return default


def iso(value):
    return dt.datetime.fromtimestamp(value,dt.timezone.utc).isoformat() if isinstance(value,(int,float)) and value else None


def commands(db,task_id,limit=100):
    try:
        values=db.execute('SELECT * FROM task_commands WHERE task_id=? ORDER BY number DESC LIMIT ?',(task_id,limit)).fetchall()
    except sqlite3.OperationalError:return []
    return [dict(r,args=parse(r['args'],{}),outcome=parse(r['outcome'],{})) for r in reversed(values)]


def checks_for(row):
    cp=parse(row.get('checkpoint'),{}) or {};result=parse(row.get('result'),{}) or {}
    checks=list(cp.get('checks') or [])+list(result.get('verification') or [])
    for h in cp.get('history',[]):
        if h.get('action')=='run_check' and isinstance(h.get('outcome'),dict):checks.append(h['outcome'])
    seen=set();out=[]
    for c in checks:
        if not isinstance(c,dict):continue
        identity=c.get('execution_id') or json.dumps(c,sort_keys=True)
        if identity not in seen:seen.add(identity);out.append(c)
    return out


def describe(row,all_tasks,command=None,now=None):
    now=time.time() if now is None else now
    row=dict(row);cp=parse(row.get('checkpoint'),{}) or {};result=parse(row.get('result'),{}) or {}
    deps=parse(row.get('depends_on'),[]) or []
    pending=[{'id':d,'agent':all_tasks.get(d,{}).get('agent'),'state':all_tasks.get(d,{}).get('status','missing')}
             for d in deps if all_tasks.get(d,{}).get('status')!='succeeded']
    children=result.get('delegated') or []
    unfinished=[{'id':d,'agent':all_tasks.get(d,{}).get('agent'),'state':all_tasks.get(d,{}).get('status','missing')}
                for d in children if all_tasks.get(d,{}).get('status')!='succeeded']
    reason=cp.get('defer_reason');blocker=cp.get('blocker')
    history=cp.get('history') or []
    error=parse(row.get('error'))
    if not blocker and error:blocker=clean(json.dumps(error,ensure_ascii=False),1500)
    if not blocker and reason in ('provider_retry','groq_capacity_wait'):
        outage=next((h.get('outcome',{}) for h in reversed(history) if h.get('action')=='provider_retry'),{})
        failures=outage.get('failures') or ([outage] if outage else [])
        blocker='; '.join(str(f.get('provider') or 'provider')+': '+str(f.get('reason') or 'capacity_wait')+
            (' (HTTP '+str(f['status'])+')' if f.get('status') else '') for f in failures if isinstance(f,dict))
        if not blocker:blocker=reason
    if not blocker and reason=='repair_cycle':
        failures=[h.get('outcome',{}) for h in history if isinstance(h.get('outcome'),dict) and
                  (h['outcome'].get('tool_error') or h['outcome'].get('passed') is False)]
        if failures:
            bad=failures[-1];blocker=clean(bad.get('output') or bad.get('message') or bad.get('reason') or bad,1500)
    if row['status']=='running':
        state='running' if (row.get('lease_until') or 0)>now else 'lease_expired'
        reason=state
    elif pending:
        state='waiting_dependency';reason='escalated_to_maestro' if cp.get('recovery_task') else 'dependency_pending'
    elif unfinished:
        state='delegated_pending';reason='delegated_implementation_pending'
    elif row['status']=='queued' and (row.get('not_before') or 0)>now:
        state='deferred';reason=reason or 'scheduled'
    elif row['status']=='queued':state='ready';reason='ready'
    elif row['status']=='succeeded':state='reported';reason='reported'
    else:state=row['status'];reason=reason or state
    checks=checks_for(row);latest={c.get('check','unknown'):c for c in checks}
    verified=bool(latest) and all(c.get('passed') is True and c.get('simulated') is False for c in latest.values()) and not unfinished
    return {'sequence':row.get('seq'),'executionState':state,'waitReason':reason,
        'blocker':blocker,'blockedBy':pending or unfinished,'nextRetryAt':iso(row.get('not_before')) if state=='deferred' else None,
        'recoveryTask':cp.get('recovery_task'),'recoveryAgent':cp.get('recovery_agent'),
        'currentCommand':command if state=='running' and command and command['phase']=='running' else None,
        'lastCommand':command,'workspace':cp.get('workspace'),'branch':cp.get('branch'),
        'approved':verified,'hasEvidence':bool(checks),'resultSummary':clean(result.get('summary') or '',5000),
        'completionKind':'delegated_pending' if unfinished else 'verified' if verified else 'reported' if result else None}


def detail(db,task_id):
    r=db.execute('SELECT * FROM tasks WHERE task_id=?',(task_id,)).fetchone()
    if r is None:raise ValueError('tarefa_nao_encontrada')
    row=dict(r);cp=parse(row.get('checkpoint'),{}) or {};history=cp.get('history') or []
    receipts=commands(db,task_id)
    legacy=[{'number':i+1,'action':h.get('action'),'summary':clean(h.get('summary') or '',1800),
             'outcome':safe_value(h.get('outcome')),'source':'legacy_checkpoint','phase':'recorded'} for i,h in enumerate(history[-100:])]
    checks=checks_for(row);evidence=[]
    for c in checks:
        for image in c.get('screenshots') or []:
            if isinstance(image,dict):
                evidence.append({'index':len(evidence),'file':Path(image.get('file') or image.get('path') or 'screenshot.png').name,
                    'sha256':image.get('sha256'),'bytes':image.get('bytes'),'viewport':image.get('viewport'),
                    'check':c.get('check'),'passed':c.get('passed'),'executionId':c.get('execution_id')})
    tasks={r['task_id']:dict(r) for r in db.execute('SELECT task_id,agent,status FROM tasks')}
    return {'id':task_id,'agent':row['agent'],'objective':clean(row['objective'],5000),'status':row['status'],
        **describe(row,tasks,receipts[-1] if receipts else None),
        'commands':receipts,'legacyHistory':legacy,'checks':safe_value(checks),'evidence':evidence,
        'events':[{'id':r['id'],'kind':r['kind'],'at':iso(r['created_at']),'data':safe_value(parse(r['data'],{}))}
                  for r in db.execute("SELECT * FROM events WHERE task_id=? AND kind!='checkpoint' ORDER BY id DESC LIMIT 40",(task_id,))]}


def evidence(db,task_id,index):
    row=db.execute('SELECT checkpoint,result FROM tasks WHERE task_id=?',(task_id,)).fetchone()
    if not row:raise ValueError('tarefa_nao_encontrada')
    images=[i for c in checks_for(dict(row)) for i in c.get('screenshots',[]) if isinstance(i,dict)]
    if type(index) is not int or index<0 or index>=len(images):raise ValueError('evidencia_nao_encontrada')
    item=images[index];p=Path(item.get('path',''))
    roots=(Path('/var/lib/horizon-worker/projects'),Path('/var/lib/horizon-worker/workspace'))
    if not p.is_absolute() or not any(p.resolve().is_relative_to(r) for r in roots) or 'runtime' not in p.parts:
        raise ValueError('evidencia_fora_do_escopo')
    if p.is_symlink() or any(x.is_symlink() for x in p.parents) or not p.is_file() or p.stat().st_size>3_000_000:
        raise ValueError('evidencia_indisponivel')
    raw=p.read_bytes();digest=hashlib.sha256(raw).hexdigest()
    if raw[:8]!=b'\x89PNG\r\n\x1a\n' or digest!=item.get('sha256'):
        raise ValueError('evidencia_integridade_invalida')
    return {'mime':'image/png','data':base64.b64encode(raw).decode(),'sha256':digest}
