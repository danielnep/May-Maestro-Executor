'use strict';
// A GitHub Actions worker owns the browser relay, not the May ChatGPT automation.
// ChatGPT is accessed only through the existing authenticated KERNEL GUI.
// No ChatGPT API, raw cookies, private endpoints, or browser-profile creation.
const crypto = require('node:crypto');

const REPO = 'danielnep/May-Maestro-Executor';
const BROWSER_NAME = 'may-chatgpt-72h';
const CHAT_TITLE = 'Horizon | Central dos Agentes';
const PREVIEW = 'https://danielnep.github.io/app-hondas-maxvel/';
const MARKER = 'MAY_CONSULT_REQUEST_V2';
const RELAY_REPLY = 'MAY_CONSULT_REPLY_V2';
const AGENTS = new Set([
  'may-maestro','horizon-project-studio','software-architect',
  'frontend-engineer','backend-engineer','mobile-app-builder','ui-ux-designer',
  'horizon-ui-guardian','database-engineer','integration-engineer',
  'qa-engineer','security-engineer','devops-engineer','horizon-evolution-agent',
  'horizon-skills','corregedoria-may','may-persona'
]);

function check(condition, description) {
  if (!condition) throw new Error(description);
}
function sha(input) {
  return crypto.createHash('sha256').update(input).digest('hex').slice(0,20);
}
function safeText(value, max=3000) {
  check(typeof value === 'string' && value.length > 0 && value.length <= max, 'Invalid public text length');
  check(!/[\x00-\x08\x0e-\x1f]/.test(value), 'Unsafe control character');
  check(!/\b(?:bearer|password|senha|private.key|api.key|secret)\b|sk-[A-Za-z0-9_-]{15,}/i.test(value),
    'Potential private credential in public text');
  return value;
}
function parseIssue(issue) {
  if (!issue || issue.pull_request || typeof issue.title !== 'string' ||
      !issue.title.startsWith('[May consultation] ')) return null;
  const body = issue.body || '';
  const v2 = body.startsWith(MARKER + '\n');
  const v1 = body.startsWith('MAY_CONSULT_REQUEST_V1\n');
  if (!v1 && !v2) return null;
  const value = key => (body.match(new RegExp('^' + key + ': ([^\\r\\n]+)', 'm')) || [])[1];
  const ticket = value('ticket') || issue.title.slice('[May consultation] '.length);
  const agent = value('agent') || (v1 ? 'may-maestro' : '');
  const reason = value('reason') || 'specialist_question';
  const question = body.split('Technical question:\n')[1]?.split('\n\n')[0]?.trim();
  if (!AGENTS.has(agent) || !/^[a-f0-9]{16,24}$/.test(ticket) ||
      issue.title !== '[May consultation] ' + ticket ||
      !['providers_unavailable','specialist_question'].includes(reason)) return null;
  if (typeof question !== 'string' || question.length < 12 || question.length > 1400) return null;
  try {safeText(question, 1400);} catch {return null;}
  return {number:issue.number, agent, ticket, reason, question,
    marker:v2 ? RELAY_REPLY : 'MAY_CONSULT_REPLY_V1',
    preview:body.includes(PREVIEW) && agent === 'horizon-ui-guardian' ? PREVIEW : null};
}
function signature(roster) {
  check(roster?.schema === 'horizon.agent.roster.v1' && Array.isArray(roster.agents), 'Invalid roster');
  return roster.agents.map(a=>[a.agent,a.status,a.task_ref || '',a.queued,a.running,a.finished,a.blocked].join(':')).join('|');
}
function latestEvents(files, cursor) {
  const all = [];
  for (const f of files) {
    if (f.schema !== 'horizon.agent.attendance.v1' || typeof f.request_id !== 'string') continue;
    const last = Number(cursor.last_ids?.[f.request_id] || 0);
    for (const event of f.events || []) {
      if (Number.isInteger(event.id) && event.id > last) all.push({...event,request_id:f.request_id});
    }
  }
  return all.sort((a,b)=>a.id-b.id || a.at.localeCompare(b.at)).slice(0,16);
}
function pointMessage(events, roster, cursor) {
  const sig = signature(roster);
  const changed = sig !== cursor.team_signature;
  if (!events.length && !changed) return null;
  const stamp = sha(JSON.stringify(events.map(e=>[e.request_id,e.id]))+'|'+(changed?sig:''));
  const lines=['[HORIZON PONTO V1]', 'LOTE: '+stamp];
  for (const e of events) {
    const id=e.request_id;
    check(/^[a-z0-9][a-z0-9-]{0,69}$/.test(id), 'Unsafe attendance request id');
    check(AGENTS.has(e.agent) && /^[a-f0-9]{12}$/.test(e.ref), 'Unsafe attendance identity');
    lines.push('AGENTE: '+e.agent+' | TAREFA: '+e.ref+' | request_id '+id+
      ' | ESTADO: '+e.status+' | HORA UTC: '+e.at+' | ID '+e.id);
    lines.push('EVIDÊNCIA: https://github.com/'+REPO+'/blob/main/tasks/attendance/'+id+'.json');
  }
  if (changed) {
    lines.push('EQUIPE: '+roster.agents.map(a=>a.agent+'='+a.status).join('; '));
    lines.push('EVIDÊNCIA EQUIPE: https://github.com/'+REPO+'/blob/main/tasks/attendance/equipe.json');
  }
  lines.push('Fonte: controlador real; concluído não equivale a QA aprovado ou deploy.');
  return {message:lines.join('\n'),stamp,newSignature:sig,events};
}
class GitHub {
  constructor(token) { check(token, 'GITHUB_TOKEN missing'); this.token=token; }
  async request(method,path,body) {
    const response=await fetch('https://api.github.com/repos/'+REPO+path,{
      method,
      headers:{'Authorization':'Bearer '+this.token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28','User-Agent':'Horizon-Agent-Central-Relay',
        ...(body ? {'Content-Type':'application/json'} : {})},
      body:body ? JSON.stringify(body):undefined
    });
    if(!response.ok) throw new Error('GitHub HTTP '+response.status+' on '+method+' '+path.split('?')[0]);
    if(response.status===204)return null;
    return response.json();
  }
  async file(path) {
    const x=await this.request('GET','/contents/'+path+'?ref=main');
    return {sha:x.sha,data:JSON.parse(Buffer.from(x.content.replace(/\s/g,''),'base64').toString('utf8'))};
  }
  async attendance() {
    const base='tasks/attendance/';
    const list=await this.request('GET','/contents/tasks/attendance?ref=main');
    const docs=await Promise.all(list.filter(x=>x.name.endsWith('.json') &&
      !['chat-cursor.json','equipe.json'].includes(x.name)).map(x=>this.file(base+x.name)));
    const [cursor,roster]=await Promise.all([this.file(base+'chat-cursor.json'),this.file(base+'equipe.json')]);
    return {cursor,roster:roster.data,files:docs.map(x=>x.data)};
  }
  async updateCursor(shaValue,data) {
    const path='tasks/attendance/chat-cursor.json';
    return this.request('PUT','/contents/'+path,{sha:shaValue,branch:'main',
      message:'chore(attendance): confirm agent-owned Central delivery [skip ci]',
      content:Buffer.from(JSON.stringify(data,null,2)+'\n').toString('base64')});
  }
  async issues() {
    const all=[];
    for(let page=1;page<=5;page++){
      const rows=await this.request('GET','/issues?state=open&sort=created&direction=asc&per_page=100&page='+page);
      all.push(...rows.filter(i=>!i.pull_request));
      if(rows.length<100) break;
    }
    return all;
  }
  async comments(number) {
    const all=[];
    for(let page=1;page<=10;page++){
      const rows=await this.request('GET','/issues/'+number+'/comments?per_page=100&page='+page);
      all.push(...rows);
      if(rows.length<100)return all;
    }
    throw new Error('GitHub comment history too large to establish unique reply');
  }
  async comment(number,body) {
    return this.request('POST','/issues/'+number+'/comments',{body});
  }
  async close(number) {return this.request('PATCH','/issues/'+number,{state:'closed'});}
}


class CentralBrowser {
  constructor(kernel) {this.kernel=kernel;this.name=BROWSER_NAME;}
  async exec(code) {
    const reply=await this.kernel.browsers.playwright.execute(this.name,{code,timeout_sec:30});
    check(reply?.success===true,'Browser refused inspection: '+String(reply?.error||'unknown').slice(0,150));
    return reply.result;
  }
  async inspect(marker,question) {
    const code=[
      'const ps=await Promise.all(context.pages().map(async p=>({p,t:await p.title().catch(()=>""),u:p.url()})));',
      'const found=ps.filter(x=>x.t==='+JSON.stringify(CHAT_TITLE)+'&&/^https:\\/\\/chatgpt\\.com\\/c\\//.test(x.u));',
      'if(new Set(found.map(x=>x.u)).size!==1)return{ready:false,reason:"single_existing_chat_not_found"};',
      'const p=found[0].p;await p.bringToFront();',
      'const e=p.getByRole("textbox",{name:"Ask ChatGPT"});',
      'if(await e.count()!==1)return{ready:false,reason:"composer_missing"};',
      'const turns=await p.locator("main [data-talvt-turn-state]").allTextContents();',
      'const marker='+JSON.stringify(marker||'')+';',
      'const question='+JSON.stringify(question||'')+';',
      'const match=marker?turns.findLast(x=>x.includes(marker)):null;',
      'let tail="";if(match&&question){const i=match.lastIndexOf(question);if(i>=0)tail=match.slice(i+question.length).trim().slice(0,3500);}',
      'const b=await e.boundingBox();',
      'return {ready:true,composer:(await e.innerText()).trim().slice(0,180),',
      'box:b?{x:Math.round(b.x+b.width/2),y:Math.round(b.y+b.height/2)}:null,posted:!!match,tail};'
    ].join('\n');
    return this.exec(code);
  }
  async send(message,marker) {
    safeText(message,12000);
    const initial=await this.inspect(marker,'');
    check(initial.ready,'Central unavailable: '+initial.reason);
    if(initial.posted)return 'already_visible';
    check(!initial.composer,'Unsent text exists; preserve draft');
    check(initial.box,'Composer not visible');
    await this.kernel.browsers.computer.clickMouse(this.name,initial.box);
    await this.kernel.browsers.computer.typeText(this.name,{text:message});
    const draft=await this.inspect(marker,'');
    check(draft.composer.includes(marker),'Browser typing not confirmed');
    await this.kernel.browsers.computer.pressKey(this.name,{keys:['Return']});
    for(let i=0;i<12;i++){
      await new Promise(resolve=>setTimeout(resolve,1000));
      const state=await this.inspect(marker,'');
      if(state.ready&&state.posted&&!state.composer)return 'posted';
    }
    throw new Error('Chat delivery uncertain; reconcile before retrying');
  }
  async reply(ticket,question) {
    for(let i=0;i<65;i++){
      const state=await this.inspect('TICKET: '+ticket,question);
      check(state.ready,'Central unavailable during response');
      if(state.posted&&state.tail&&state.tail.length>=8){
        // Never return a half-streamed model answer as a completed consultation.
        await new Promise(resolve=>setTimeout(resolve,1700));
        const again=await this.inspect('TICKET: '+ticket,question);
        if(again.ready && again.posted && again.tail===state.tail)
          return safeText(again.tail,3500);
      }
      await new Promise(resolve=>setTimeout(resolve,1600));
    }
    throw new Error('No verified ChatGPT answer visible');
  }
  async inspectApprovedPreview(){
    const code=[
      'const p=await context.newPage();try{',
      'await p.setViewportSize({width:390,height:844});',
      'const r=await p.goto('+JSON.stringify(PREVIEW)+',{waitUntil:"domcontentloaded",timeout:15000});',
      'const value=await p.evaluate(()=>({title:document.title,width:innerWidth,scrollWidth:document.documentElement.scrollWidth,',
      'visibleButtons:[...document.querySelectorAll("button")].filter(e=>e.getBoundingClientRect().width>0).length}));',
      'return {status:r.status(),...value};',
      '}finally{await p.close();}'
    ].join('\n');
    return this.exec(code);
  }
}
function normalizeAdvice(answer){
  const normalized=answer.replace(/^\x60{3}(?:json)?\s*/,'').replace(/\s*\x60{3}$/,'').trim();
  safeText(normalized,3500);
  try{
    const obj=JSON.parse(normalized);
    if(obj&&!Array.isArray(obj)&&Object.keys(obj).sort().join(',')==='action,args,summary'&&
       typeof obj.action==='string'&&obj.args&&typeof obj.args==='object'&&
       !Array.isArray(obj.args)&&typeof obj.summary==='string')
      return JSON.stringify({action:obj.action,args:obj.args,summary:obj.summary});
  }catch{}
  return normalized.slice(0,2800);
}
async function relayAttendance(gh,browser){
  const snapshot=await gh.attendance();
  const data=snapshot.cursor.data;
  check(data.schema==='horizon.agent.attendance.cursor.v1','Invalid attendance cursor');
  const packet=pointMessage(latestEvents(snapshot.files,data),snapshot.roster,data);
  if(!packet)return {status:'unchanged'};
  const result=await browser.send(packet.message,'LOTE: '+packet.stamp);
  check(result==='posted'||result==='already_visible','Attendance not confirmed');
  const latest=await gh.file('tasks/attendance/chat-cursor.json');
  const cursor=latest.data;
  for(const e of packet.events)
    cursor.last_ids[e.request_id]=Math.max(Number(cursor.last_ids[e.request_id]||0),e.id);
  if(packet.newSignature!==data.team_signature)cursor.team_signature=packet.newSignature;
  await gh.updateCursor(latest.sha,cursor);
  const updated=await gh.file('tasks/attendance/chat-cursor.json');
  check(packet.events.every(e=>updated.data.last_ids[e.request_id]>=e.id),'Cursor write not confirmed');
  return {status:'delivered',count:packet.events.length,stamp:packet.stamp};
}


async function relayConsultations(gh,browser){
  const candidates=(await gh.issues()).map(parseIssue).filter(Boolean).slice(0,3);
  const outcome=[];
  for(const issue of candidates){
    const prior=await gh.comments(issue.number);
    if(prior.some(c=>['danielnep','github-actions[bot]'].includes(c.user?.login)&&
      c.body?.startsWith(issue.marker+'\n'))){
      outcome.push({issue:issue.number,status:'already_answered'});continue;
    }
    const inFlight='HORIZON_RELAY_INFLIGHT_V1 '+issue.ticket;
    const previouslyStarted=prior.some(c=>c.user?.login==='github-actions[bot]'&&
      c.body?.startsWith(inFlight));
    let question='[HORIZON CONSULTA V2]\nAGENTE: '+issue.agent+
      '\nTICKET: '+issue.ticket+'\nTIPO: '+issue.reason+'\nPERGUNTA: '+issue.question;
    if(issue.preview){
      const proof=await browser.inspectApprovedPreview();
      question+='\nPREVIEW PÚBLICO APROVADO: '+issue.preview+
        '\nPROVA DE NAVEGADOR (somente geometria): '+JSON.stringify(proof);
    }
    if(!previouslyStarted){
      await gh.comment(issue.number,inFlight+
        '\nApenas despacho iniciado pelo executor; ainda não é uma resposta.');
    }
    try{
      const alreadyVisible=(await browser.inspect('TICKET: '+issue.ticket,'')).posted;
      if(previouslyStarted&&!alreadyVisible){
        outcome.push({issue:issue.number,status:'reconcile_required'});continue;
      }
      if(!alreadyVisible)
        await browser.send(question,'TICKET: '+issue.ticket);
      const realAnswer=normalizeAdvice(await browser.reply(issue.ticket,issue.question));
      const comments=await gh.comments(issue.number);
      if(comments.some(c=>['danielnep','github-actions[bot]'].includes(c.user?.login)&&
         c.body?.startsWith(issue.marker+'\n'))){
        outcome.push({issue:issue.number,status:'answered_concurrently'});continue;
      }
      const posted=await gh.comment(issue.number,issue.marker+'\n'+realAnswer);
      const confirmed=await gh.comments(issue.number);
      check(confirmed.some(c=>c.id===posted.id&&c.body?.startsWith(issue.marker+'\n')),
        'GitHub reply not confirmed');
      await gh.close(issue.number);
      outcome.push({issue:issue.number,status:'replied'});
    }catch(error){
      outcome.push({issue:issue.number,status:'pending',reason:error.message.slice(0,130)});
    }
  }
  return outcome;
}
async function run({kernel,gh}){
  const matches=[];
  for await(const item of kernel.browsers.list({query:BROWSER_NAME,status:'active'})){
    if(item.name===BROWSER_NAME)matches.push(item);
    if(matches.length>1)break;
  }
  check(matches.length===1,'Expected one active, named KERNEL session');
  const browser=new CentralBrowser(kernel);
  const probe=await browser.inspect('','');
  check(probe.ready,'Existing authenticated Central is unavailable');
  const point=await relayAttendance(gh,browser);
  const consultations=await relayConsultations(gh,browser);
  return {point,consultations};
}
if(require.main===module){
  (async()=>{
    if(!process.env.KERNEL_API_KEY){
      console.log('Relay not activated: KERNEL_API_KEY secret absent');
      return;
    }
    check(process.env.GITHUB_TOKEN,'GITHUB_TOKEN secret absent');
    const KernelSDK=require('@onkernel/sdk').default;
    const result=await run({kernel:new KernelSDK({apiKey:process.env.KERNEL_API_KEY}),
      gh:new GitHub(process.env.GITHUB_TOKEN)});
    console.log(JSON.stringify(result));
  })().catch(error=>{
    console.error('Horizon independent relay blocked:',error.message);
    process.exitCode=1;
  });
}
module.exports={parseIssue,signature,latestEvents,pointMessage,normalizeAdvice,
  GitHub,CentralBrowser,relayAttendance,relayConsultations,run};

