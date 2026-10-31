"""Durable operator approvals. A decision binds to an immutable proposal fingerprint.

No model text or ChatGPT browser action can bypass operator authentication.
External adapters must authenticate the operator before calling decide().
"""
import hashlib
from contextlib import contextmanager
import json
import sqlite3
import time
import uuid
from pathlib import Path

STATES = ('awaiting_decision','approved','executing','completed','rejected','failed')

def canonical_proposal(project, change, before_sha, after_sha, tests):
    if not all(isinstance(v, str) and v.strip() for v in (project,change,before_sha,after_sha)):
        raise ValueError('Project, change and both screenshot digests are mandatory')
    if not all(len(x)==64 and all(c in '0123456789abcdef' for c in x) for x in (before_sha,after_sha)):
        raise ValueError('Screenshots require SHA-256 hex digests')
    if not isinstance(tests,dict):raise ValueError('Tests must be a record')
    content={'project':project,'change':change,'before_sha256':before_sha,'after_sha256':after_sha,'tests':tests}
    raw=json.dumps(content,sort_keys=True,ensure_ascii=False,separators=(',',':'))
    return hashlib.sha256(raw.encode()).hexdigest(),raw

class ApprovalQueue:
    def __init__(self,path):
        self.path=str(path)
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.path,timeout=10,isolation_level=None)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA busy_timeout=10000')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
CREATE TABLE IF NOT EXISTS approvals (
 id TEXT PRIMARY KEY, digest TEXT NOT NULL, proposal TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('awaiting_decision','approved','executing','completed','rejected','failed')),
 decision_by TEXT, created_at INTEGER NOT NULL, changed_at INTEGER NOT NULL, result TEXT
);
CREATE TABLE IF NOT EXISTS events (
 id TEXT PRIMARY KEY, approval_id TEXT NOT NULL REFERENCES approvals(id),
 kind TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
 created_at INTEGER NOT NULL, delivered_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_approval_status ON approvals(status);
CREATE INDEX IF NOT EXISTS idx_event_pending ON events(status,created_at);
''')
    @contextmanager
    def transaction(self):
        # Serialize check-and-insert / decision transitions across processes.
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
        except BaseException:
            self.db.execute('ROLLBACK')
            raise
        else:
            self.db.execute('COMMIT')
    def _event(self,id,kind,payload):
        self.db.execute('INSERT INTO events(id,approval_id,kind,payload,created_at) VALUES(?,?,?,?,?)',
            (uuid.uuid4().hex,id,kind,json.dumps(payload,ensure_ascii=False,sort_keys=True),int(time.time())))
    def propose(self,project,change,before_sha,after_sha,tests,*,ticket):
        digest,proposal=canonical_proposal(project,change,before_sha,after_sha,tests)
        if not ticket or len(ticket)>120:raise ValueError('Stable ticket is required')
        with self.transaction():
            existing=self.db.execute('SELECT * FROM approvals WHERE id=?',(ticket,)).fetchone()
            if existing:
                if existing['digest']!=digest:raise ValueError('Changed proposal requires a NEW ticket and approval')
                return dict(existing)
            now=int(time.time())
            self.db.execute('INSERT INTO approvals(id,digest,proposal,status,created_at,changed_at) VALUES(?,?,?,?,?,?)',
                (ticket,digest,proposal,'awaiting_decision',now,now))
            self._event(ticket,'approval_requested',{'ticket':ticket,'digest':digest,'proposal':json.loads(proposal)})
        return self.get(ticket)
    def get(self,ticket):
        r=self.db.execute('SELECT * FROM approvals WHERE id=?',(ticket,)).fetchone()
        return dict(r) if r else None
    def decide(self,ticket,digest,approved,*,actor):
        if not actor or not isinstance(approved,bool):raise ValueError('Authenticated actor and boolean decision required')
        with self.transaction():
            cur=self.db.execute('UPDATE approvals SET status=?,decision_by=?,changed_at=? WHERE id=? AND digest=? AND status=?',
                ('approved' if approved else 'rejected',actor,int(time.time()),ticket,digest,'awaiting_decision'))
            if cur.rowcount!=1:
                raise ValueError('Unknown ticket, stale proposal, or decision already taken')
            self._event(ticket,'decision_recorded',{'approved':approved,'actor':actor,'digest':digest})
        return self.get(ticket)
    def transition(self,ticket,digest,to_status,*,result=''):
        allowed={'approved':'executing','executing':'completed','failed':'executing'}
        if to_status not in ('executing','completed','failed'):raise ValueError('Invalid execution state')
        with self.transaction():
            record=self.get(ticket)
            if not record or record['digest']!=digest:raise ValueError('Invalid ticket/digest')
            if not (allowed.get(record['status'])==to_status or (record['status']=='executing' and to_status=='failed')):
                raise ValueError('Illegal state transition')
            self.db.execute('UPDATE approvals SET status=?,changed_at=?,result=? WHERE id=?',
                (to_status,int(time.time()),result[:1000],ticket))
            self._event(ticket,'execution_'+to_status,{'result':result[:1000]})
        return self.get(ticket)
    def pending_events(self,limit=30):
        return [dict(r) for r in self.db.execute('SELECT * FROM events WHERE status=? ORDER BY created_at,id LIMIT ?',('pending',limit))]
    def begin_delivery(self,event_id):
        """Claim once. Incomplete attempts stay sending until human reconciliation."""
        with self.transaction():
            return self.db.execute('UPDATE events SET status=? WHERE id=? AND status=?',
                ('sending',event_id,'pending')).rowcount==1
    def confirm_delivery(self,event_id,*,receipt):
        if not isinstance(receipt,str) or not 5<=len(receipt)<=200:
            raise ValueError('A verified visible message receipt is required')
        with self.transaction():
            event=self.db.execute('SELECT payload FROM events WHERE id=? AND status=?',
                 (event_id,'sending')).fetchone()
            if not event:return False
            packet=json.loads(event['payload'])
            packet['delivery_receipt']=receipt
            return self.db.execute('UPDATE events SET status=?,delivered_at=?,payload=? WHERE id=? AND status=?',
                ('delivered',int(time.time()),json.dumps(packet,ensure_ascii=False),event_id,'sending')).rowcount==1
    def delivery_uncertain(self,event_id):
        with self.transaction():
            return self.db.execute('UPDATE events SET status=? WHERE id=? AND status=?',
                ('uncertain',event_id,'sending')).rowcount==1
    def unresolved_delivery(self):
        return [dict(r) for r in self.db.execute('SELECT * FROM events WHERE status IN (?,?) ORDER BY created_at,id',
                                                 ('sending','uncertain'))]
    def close(self):self.db.close()
