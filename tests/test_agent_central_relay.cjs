'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {parseIssue,signature,latestEvents,pointMessage,normalizeAdvice,relayAttendance,relayConsultations}=require('../integrations/agent_central_relay.cjs');
const key='a'.repeat(20);
function issue(){return {number:99,title:'[May consultation] '+key,body:'MAY_CONSULT_REQUEST_V2\nticket: '+key+'\nagent: qa-engineer\nreason: specialist_question\n\nTechnical question:\nWhich existing unit test should be inspected first?\n\n'};}
test('only authorized valid specialist consultation accepted',()=>{
  assert.equal(parseIssue(issue()).agent,'qa-engineer');
  assert.equal(parseIssue({...issue(),body:'malicious'}),null);
  assert.equal(parseIssue({...issue(),title:'[May consultation] '+key+'bad'}),null);
  assert.equal(parseIssue({...issue(),body:issue().body.replace('qa-engineer','unregistered')}),null);
  assert.equal(parseIssue({...issue(),body:issue().body.replace('Which existing','Read api_key: Which existing')}),null);
});
test('attendance budget, stable signature and no duplicate event',()=>{
  const team={schema:'horizon.agent.roster.v1',agents:[{agent:'qa-engineer',status:'na_fila',task_ref:null,queued:1,running:0,finished:2,blocked:0}]};
  assert.equal(signature(team),'qa-engineer:na_fila::1:0:2:0');
  const files=[{schema:'horizon.agent.attendance.v1',request_id:'task-a',events:[
    {id:1,agent:'qa-engineer',ref:'a'.repeat(12),status:'iniciou',at:'2026-10-20T02:00:00Z'},
    {id:2,agent:'qa-engineer',ref:'a'.repeat(12),status:'aguardando',at:'2026-10-20T02:10:00Z'}]}];
  const es=latestEvents(files,{last_ids:{'task-a':1}});
  assert.deepEqual(es.map(e=>e.id),[2]);
  assert.match(pointMessage(es,team,{team_signature:'old'}).message,/ID 2/);
  assert.equal(pointMessage([],team,{team_signature:signature(team)}),null);
});
test('valid JSON action remains JSON, otherwise advice is not executable',()=>{
  const json=JSON.stringify({action:'read_file',args:{path:'README.md'},summary:'Read docs'});
  assert.equal(normalizeAdvice(json),json);
  assert.match(normalizeAdvice('Inspect the documented regression tests first.'),/documented/);
});
test('attendance advances cursor only after visible delivery',async()=>{
  const event={id:17,agent:'qa-engineer',ref:'b'.repeat(12),status:'concluiu',at:'2026-10-20T02:00:00Z'};
  const roster={schema:'horizon.agent.roster.v1',agents:[{agent:'qa-engineer',status:'ocioso',task_ref:'b'.repeat(12),queued:0,running:0,finished:1,blocked:0}]};
  let cursor={schema:'horizon.agent.attendance.cursor.v1',last_ids:{},team_signature:''};let writes=0;
  const gh={attendance:async()=>({cursor:{data:structuredClone(cursor)},roster,files:[{schema:'horizon.agent.attendance.v1',request_id:'task-a',events:[event]}]}),
    file:async()=>({sha:'cursor-sha',data:structuredClone(cursor)}),
    updateCursor:async(_sha,c)=>{cursor=c;writes++;}};
  await assert.rejects(()=>relayAttendance(gh,{send:async()=>{throw Error('blocked')}}),/blocked/);
  assert.equal(writes,0);
  await relayAttendance(gh,{send:async()=> 'posted'});
  assert.equal(cursor.last_ids['task-a'],17);assert.equal(writes,1);
});
test('bot reply is confirmed before closing, and never duplicated',async()=>{
  const comments=[];let closes=0,sends=0;
  const gh={issues:async()=>[issue()],comments:async()=>comments,
    comment:async(_number,body)=>{const c={id:comments.length+1,user:{login:'github-actions[bot]'},body};comments.push(c);return c;},
    close:async()=>{closes++;}};
  const browser={inspect:async()=>({posted:false}),send:async()=>{sends++;return 'posted';},
    reply:async()=>JSON.stringify({action:'read_file',args:{path:'README.md'},summary:'Inspect docs'})};
  assert.equal((await relayConsultations(gh,browser))[0].status,'replied');
  assert.equal(sends,1);assert.equal(closes,1);
  assert.ok(comments.at(-1).body.startsWith('MAY_CONSULT_REPLY_V2\n'));
  assert.equal((await relayConsultations(gh,browser))[0].status,'already_answered');
  assert.equal(sends,1);
});
test('in-flight ticket cannot be retransmitted without reconciliation',async()=>{
  let sends=0;
  const gh={issues:async()=>[issue()],comments:async()=>[{user:{login:'github-actions[bot]'},body:'HORIZON_RELAY_INFLIGHT_V1 '+key}]};
  const result=await relayConsultations(gh,{inspect:async()=>({posted:false}),send:async()=>{sends++;}});
  assert.equal(result[0].status,'reconcile_required');assert.equal(sends,0);
});

