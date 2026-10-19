'use strict';
// Trusted generic harness: JSON describes interactions, never JavaScript or shell.
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const configPath='/project/horizon.browser.json';const cfg=fs.existsSync(configPath)?JSON.parse(fs.readFileSync(configPath,'utf8')):{};
if(cfg.profile==='station-synthetic-v1'){require('./station-proof.cjs');}else{
const {chromium}=require('/opt/visual/node_modules/playwright');const out='/evidence';fs.mkdirSync(out,{recursive:true});const report={check:'browser-playwright',profile:cfg.steps?'configured-flows-v2':'generic-static-v1',tests:0,passed:false,functional_scenarios:0,checks:[],screenshots:[],layout:[],consoleErrors:[],pageErrors:[],scope:cfg.steps?'Configured scenarios only':'Smoke only; no functional scenarios configured'};let browser,server,lastPage=null,lastViewport=null;
function check(name,passed,details={}){report.tests++;report.checks.push({name,passed,...details});assert.ok(passed,name);}
async function capture(page,viewport,suffix){
 if(report.screenshots.length>=(suffix.startsWith('stage-')?18:20))throw Error('Up to 18 intermediate frames plus 2 final viewport captures');
 if(!/^[a-z0-9][a-z0-9-]{0,63}$/.test(suffix))throw Error('Invalid screenshot stage name');
 const file=viewport.name+'-'+suffix+'.png';
 if(report.screenshots.some(item=>item.file===file))throw Error('Duplicate screenshot stage name');
 await page.screenshot({path:path.join(out,file),fullPage:true,animations:'disabled'});
 const data=fs.readFileSync(path.join(out,file));
 report.screenshots.push({file,viewport,bytes:data.length,
   sha256:crypto.createHash('sha256').update(data).digest('hex')});
}
function locator(page,s){if(!s||typeof s!=='object')throw Error('A locator object is required');if(typeof s.role==='string'&&typeof s.name==='string')return page.getByRole(s.role,{name:s.name,exact:true});if(typeof s.label==='string')return page.getByLabel(s.label,{exact:true});if(typeof s.test_id==='string')return page.getByTestId(s.test_id);throw Error('Use a role/name, label or test_id locator');}
(async()=>{try{
server=http.createServer((req,res)=>{let name;try{name=decodeURIComponent(new URL(req.url,'http://127.0.0.1').pathname);}catch{res.writeHead(400);return res.end();}if(name==='/favicon.ico'){res.writeHead(204);return res.end();}const file=path.resolve('/project','.'+(name==='/'?'/index.html':name));if(!file.startsWith('/project/')){res.writeHead(403);return res.end();}fs.readFile(file,(err,data)=>{if(err){res.writeHead(404);return res.end('Not found');}res.writeHead(200,{'Content-Type':({'.html':'text/html; charset=utf-8','.css':'text/css','.js':'text/javascript','.json':'application/json'})[path.extname(file)]||'application/octet-stream','Cache-Control':'no-store'});res.end(data);});});await new Promise(r=>server.listen(4173,'127.0.0.1',r));browser=await chromium.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
const steps=cfg.steps||[];if(!Array.isArray(steps)||steps.length>100)throw Error('steps must be an array of at most100 supported interactions');
for(const viewport of [{name:'desktop',width:1440,height:960},{name:'mobile',width:390,height:844}]){const context=await browser.newContext({viewport:{width:viewport.width,height:viewport.height},reducedMotion:'reduce'});await context.route('**/*',r=>new URL(r.request().url()).hostname==='127.0.0.1'?r.continue():r.abort());const page=await context.newPage();lastPage=page;lastViewport=viewport;page.on('console',m=>{if(m.type()==='error')report.consoleErrors.push(m.text());});page.on('pageerror',e=>report.pageErrors.push(e.name));await page.goto('http://127.0.0.1:4173/');
if(cfg.ready){await locator(page,cfg.ready).waitFor({state:'visible',timeout:10000});}
// Initial screen is evaluated by DOM only, not sent as a screenshot.
check(viewport.name+': meaningful rendered page',(await page.locator('body').innerText()).trim().length>10);
for(const [position,step] of steps.entries()){
 if(step.viewports!==undefined){if(!Array.isArray(step.viewports)||!step.viewports.length||step.viewports.some(name=>!['mobile','desktop'].includes(name)))throw Error('Invalid viewports selector');if(!step.viewports.includes(viewport.name))continue;}
 switch(step.action){case'click':await locator(page,step.target).click({timeout:8000});break;case'fill':assert.equal(typeof step.value,'string');await locator(page,step.target).fill(step.value);break;case'press':assert.ok(typeof step.key==='string'&&step.key.length<50);await page.keyboard.press(step.key);break;case'expect_text':assert.equal(typeof step.text,'string');await page.getByText(step.text,{exact:step.exact!==false}).first().waitFor();break;
 case'expect_visible':await locator(page,step.target).waitFor({state:'visible',timeout:8000});break;
 case'expect_hidden':await locator(page,step.target).waitFor({state:'hidden',timeout:8000});break;
 case'expect_attribute':{
  assert.ok(typeof step.attribute==='string'&&/^[a-zA-Z][a-zA-Z0-9_-]{0,79}$/.test(step.attribute));
  assert.equal(typeof step.value,'string');
  const value=await locator(page,step.target).getAttribute(step.attribute);
  assert.ok(step.contains===true?typeof value==='string'&&value.includes(step.value):value===step.value,'Attribute mismatch at stage '+position);
  break;
 }
 case'drag':{
  const box=await locator(page,step.target).boundingBox();assert.ok(box&&box.width>=5&&box.height>=5,'Cannot drag hidden target');
  assert.ok(Number.isInteger(step.dx)&&Number.isInteger(step.dy)&&Math.abs(step.dx)<=800&&Math.abs(step.dy)<=800&&(step.dx!==0||step.dy!==0),'Drag distances must be non-zero integers within 800 pixels');
  const x=box.x+box.width/2,y=box.y+box.height/2;
  await page.mouse.move(x,y);await page.mouse.down();await page.mouse.move(x+step.dx,y+step.dy,{steps:12});await page.mouse.up();
  break;
 }
 case'capture':{
  const name=typeof step.name==='string'&&/^[a-z0-9][a-z0-9-]{0,35}$/.test(step.name)?step.name:'stage';
  await capture(page,viewport,'stage-'+position+'-'+name);
  break;
 }case'offline':assert.equal(typeof step.value,'boolean');await context.setOffline(step.value);break;case'navigate':assert.ok(typeof step.path==='string'&&/^\/(?!\/)|^#/.test(step.path)&&!step.path.includes('..'));await page.goto('http://127.0.0.1:4173/'+step.path.replace(/^\//,''));break;default:throw Error('Unsupported interaction action');}if(['click','fill','press','drag','navigate','offline'].includes(step.action))report.functional_scenarios++;
 check(viewport.name+': '+step.action+': '+position,true);
} // Interactions produce structured checks; no PNG on each step.
const metrics=await page.evaluate(()=>{const names=[];for(const e of document.querySelectorAll('button,input,select,textarea,a[href]')){if(!(e.offsetWidth||e.offsetHeight||e.getClientRects().length)||e.type==='hidden')continue;const named=e.getAttribute('aria-label')||e.getAttribute('aria-labelledby')||e.textContent.trim()||Array.from(e.labels||[]).map(l=>l.textContent).join('');if(!named)names.push(e.tagName);}const blocking=[],warnings=[];let scanned=0;
for(const el of document.querySelectorAll('button,input,select,textarea,a[href],[role="button"]')){
 if(scanned>=150)break;
 const style=getComputedStyle(el),r=el.getBoundingClientRect();
 if(style.display==='none'||style.visibility==='hidden'||style.pointerEvents==='none'||!r.width||!r.height)continue;
 if(r.bottom<=0||r.top>=innerHeight)continue;
 scanned++;
 const label=String(el.getAttribute('aria-label')||el.textContent||el.tagName).trim().slice(0,48);
 if(r.left< -1||r.right>innerWidth+1)blocking.push({kind:'horizontal_clip',label});
 if(innerWidth<=600&&(r.width<40||r.height<40))warnings.push({kind:'small_touch_target',label});
 if(r.top>=0&&r.bottom<=innerHeight){
   const x=Math.max(0,Math.min(innerWidth-1,r.left+r.width/2));
   const y=Math.max(0,Math.min(innerHeight-1,r.top+r.height/2));
   const hit=document.elementFromPoint(x,y);
   if(hit&&!el.contains(hit)&&!hit.contains(el))blocking.push({kind:'covered_control',label});
 }
}
return{viewport:innerWidth,scroll:document.documentElement.scrollWidth,
 unnamed:names,lang:document.documentElement.lang,title:document.title,
 controlsMeasured:scanned,blocking:blocking.slice(0,20),warnings:warnings.slice(0,20)};});report.layout.push({name:viewport.name,width:viewport.width,height:viewport.height,controls_measured:metrics.controlsMeasured,blocking_issues:metrics.blocking,warnings:metrics.warnings});check(viewport.name+': no horizontal overflow',metrics.scroll<=metrics.viewport+1,metrics);check(viewport.name+': no covered or clipped controls',metrics.blocking.length===0,metrics);check(viewport.name+': basic names/language/title',!metrics.unnamed.length&&!!metrics.lang&&!!metrics.title,metrics);await capture(page,viewport,'rendered');await context.close();lastPage=null;}
check('console and runtime health',!report.consoleErrors.length&&!report.pageErrors.length);report.passed=report.tests>0&&report.checks.every(c=>c.passed===true)&&report.screenshots.length>=2&&['desktop-rendered.png','mobile-rendered.png'].every(file=>report.screenshots.some(item=>item.file===file));
 }catch(error){
 report.failure={kind:error.name,message:String(error.message).slice(0,300)};
 if(lastPage&&lastViewport){
   try{if(report.screenshots.length<20&&!report.screenshots.some(item=>item.file===lastViewport.name+'-failure.png'))await capture(lastPage,lastViewport,'failure');}
   catch(captureError){report.failure.capture_failed=String(captureError.name);}
 }
}finally{if(browser)await browser.close();if(server)await new Promise(r=>server.close(r));fs.writeFileSync(path.join(out,'manifest.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));process.exitCode=report.passed?0:1;}})();}
