"""Fit free-tier calls while retaining scope, tool contracts and actionable evidence."""
import copy
import json
from .groq_minute_budget import estimated_input_tokens


def excerpt(value,limit):
    text=value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,default=str)
    return text if len(text)<=limit else text[:max(100,limit-250)]+' [full record retained in checkpoint] '+text[-180:]


def fit(messages,budget=5200):
    if estimated_input_tokens(messages)<=budget:return messages
    result=copy.deepcopy(messages)
    for message in result:
        if message.get('role')=='system':continue
        try:ctx=json.loads(message['content'])
        except (ValueError,TypeError):
            message['content']=excerpt(message.get('content',''),3000);continue
        if not isinstance(ctx,dict):continue
        # The exact authorization scope remains intact. Bulky logs and repeated
        # inventories are retrieved from disk when needed instead of resent.
        compact={k:v for k,v in ctx.items() if k in (
            'objective','project','scope','model_policy','completed_steps','remaining_steps','warning')}
        compact['objective']=excerpt(ctx.get('objective',''),2200)
        if ctx.get('dependencies'):compact['dependencies']={k:excerpt(v,650) for k,v in list(ctx['dependencies'].items())[-4:]}
        compact['history']=[{'action':r.get('action'),'summary':excerpt(r.get('summary',''),180),
            'outcome':excerpt(r.get('outcome',{}),1200)} for r in ctx.get('history',[])[-2:]]
        compact['context_compacted']=True
        message['content']=json.dumps(compact,ensure_ascii=False,separators=(',',':'))
    if estimated_input_tokens(result)>budget:
        for message in result:
            if message.get('role')!='system':continue
            body=message.get('content','');marker='\nAllowed actions: '
            if marker in body:
                instructions,contracts=body.split(marker,1)
                message['content']=excerpt(instructions,2600)+marker+contracts
    if estimated_input_tokens(result)>budget:
        for message in result:
            if message.get('role')=='system':continue
            try:ctx=json.loads(message['content'])
            except (TypeError,ValueError):continue
            if not isinstance(ctx,dict):continue
            ctx.pop('dependencies',None)
            ctx['history']=[{'action':r.get('action'),'outcome':excerpt(r.get('outcome',''),500)} for r in ctx.get('history',[])[-1:]]
            ctx['objective']=excerpt(ctx.get('objective',''),1200)
            message['content']=json.dumps(ctx,ensure_ascii=False,separators=(',',':'))
    return result
