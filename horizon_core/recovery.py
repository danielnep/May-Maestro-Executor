"""A blocker becomes an independent AI assignment with an explicit resume gate."""
import hashlib
import json
import time
from .operations import clean, safe_value


ORCHESTRATORS={'may-maestro','horizon-supervisor'}


def escalate(state,task,worker,checkpoint,reason,detail=None,target=None):
    reason=clean(reason,180)
    detail=clean(detail or reason,1800)
    depth=int(task['payload'].get('recovery_depth',0))
    if depth>=3:
        state.checkpoint(task['task_id'],worker,{**checkpoint,'defer_reason':'recovery_exhausted',
            'blocker':detail,'requiredAction':'Resolve the recorded dependency or environment failure; inspect command receipts.'})
        state.fail(task['task_id'],worker,{'reason':'recovery_exhausted','cause':detail,'required_action':'Inspect failed recovery chain and provide missing capability or credential.'},retryable=False)
        return state.get(task['task_id'])
    target=target or ('horizon-supervisor' if task['agent']=='may-maestro' else 'may-maestro')
    if target not in ORCHESTRATORS or target==task['agent']:
        target='horizon-supervisor' if task['agent']!='horizon-supervisor' else 'may-maestro'
    identity=hashlib.sha256((task['task_id']+'|'+reason+'|'+str(depth)).encode()).hexdigest()[:20]
    review='recovery-'+identity
    payload={**task['payload'],'recovery_for':task['task_id'],'recovery_depth':depth+1,
             'blocker_reason':reason,'blocker_detail':detail}
    recent=[{'action':h.get('action'),'summary':h.get('summary'),'outcome':safe_value(h.get('outcome'))}
            for h in checkpoint.get('history',[])[-3:]]
    objective=('Resolve blocker for '+task['task_id']+'. Cause: '+detail+
       '. Diagnose from actual receipts; delegate a focused repair to the correct specialist. '
       'The original task must wait for the repair and its verification. Do not merely report the failure, '
       'repeat an unchanged failing command, ask the owner to implement, or claim success without evidence. '
       'If this is environment/credential access outside tools, record exactly the missing capability. '
       'Recent tool evidence: '+json.dumps(recent,ensure_ascii=False))[:5000]
    state.enqueue(review,target,task['project'],objective,payload=payload,priority=max(100,task.get('priority',0)+10),max_attempts=3)
    updated={**checkpoint,'defer_reason':'escalated_to_maestro','recovery_task':review,
             'blocker':detail,'recovery_agent':target,'deferred_at':time.time()}
    state.checkpoint(task['task_id'],worker,updated)
    with state._tx():
        state._owned(task['task_id'],worker,time.time())
        row=state.get(task['task_id'])
        deps=list(dict.fromkeys(row['depends_on']+[review]))
        state.db.execute("UPDATE tasks SET depends_on=?,status='queued',attempts=MAX(0,attempts-1),not_before=0,lease_owner=NULL,lease_until=NULL WHERE task_id=?",
                         (json.dumps(deps),task['task_id']))
        state._event(task['task_id'],'blocker_escalated',{'recovery_task':review,'agent':target,'reason':reason,'cause':detail})
    return state.get(task['task_id'])


def gate_original_on_children(state,task,children):
    original=task.get('payload',{}).get('recovery_for')
    if not original or not children:
        return
    with state._tx():
        row=state.get(original)
        if row and row['status']=='queued':
            deps=list(dict.fromkeys(row['depends_on']+children))
            state.db.execute('UPDATE tasks SET depends_on=? WHERE task_id=?',(json.dumps(deps),original))
            state._event(original,'repair_dependencies_added',{'tasks':children})
