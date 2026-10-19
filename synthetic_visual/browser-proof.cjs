'use strict';
// Trusted generic harness: JSON describes interactions, never JavaScript or shell.
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const configPath='/project/horizon.browser.json';const cfg=fs.existsSync(configPath)?JSON.parse(fs.readFileSync(configPath,'utf8')):{};
if(cfg.profile==='station-synthetic-v1'){require('./station-proof.cjs');}else{
const {chromium}=require('/opt/visual/node_modules/playwright');const out='/evidence';fs.mkdirSync(out,{recursive:true});const report={check:'browser-playwright',profile:'generic-static-v1',tests:0,passed:false,functional_scenarios:0,checks:[],screenshots:[],consoleErrors:[],pageErrors:[],scope:cfg.steps?'Configured scenarios only':'Smoke only; no functional scenarios configured'};let browser,server,lastPage=null,lastViewport=null;
function check(name,passed,details={}){report.tests++;report.checks.push({name,passed,...details});assert.ok(passed,name);}
async function capture(page,viewport,suffix){
 if(report.screenshots.length>=20)throw Error('Too many visual captures (max 20)');
 const file=viewport.name+'-'+suffix+'.png';
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
if(cfg.capture_initial===true){await capture(page,viewport,'initial');}
check(viewport.name+': meaningful rendered page',(await page.locator('body').innerText()).trim().length>10);
for(const [position,step] of steps.entries()){switch(step.action){case'click':await locator(page,step.target).click();break;case'fill':assert.equal(typeof step.value,'string');await locator(page,step.target).fill(step.value);break;case'press':assert.ok(typeof step.key==='string'&&step.key.length<50);await page.keyboard.press(step.key);break;case'expect_text':assert.equal(typeof step.text,'string');await page.getByText(step.text,{exact:step.exact!==false}).first().waitFor();break;case'offline':assert.equal(typeof step.value,'boolean');await context.setOffline(step.value);break;case'navigate':assert.ok(typeof step.path==='string'&&/^\/(?!\/)|^#/.test(step.path)&&!step.path.includes('..'));await page.goto('http://127.0.0.1:4173/'+step.path.replace(/^\//,''));break;default:throw Error('Unsupported interaction action');}report.functional_scenarios++;check(viewport.name+': '+step.action,true);
if(step.capture===true){await capture(page,viewport,'step-'+String(position+1).padStart(2,'0'));}}
const metrics=await page.evaluate(()=>{const names=[];for(const e of document.querySelectorAll('button,input,select,textarea,a[href]')){if(!(e.offsetWidth||e.offsetHeight||e.getClientRects().length)||e.type==='hidden')continue;const named=e.getAttribute('aria-label')||e.getAttribute('aria-labelledby')||e.textContent.trim()||Array.from(e.labels||[]).map(l=>l.textContent).join('');if(!named)names.push(e.tagName);}return{viewport:innerWidth,scroll:document.documentElement.scrollWidth,unnamed:names,lang:document.documentElement.lang,title:document.title};});check(viewport.name+': no horizontal overflow',metrics.scroll<=metrics.viewport+1,metrics);check(viewport.name+': basic names/language/title',!metrics.unnamed.length&&!!metrics.lang&&!!metrics.title,metrics);await capture(page,viewport,'rendered');await context.close();lastPage=null;}
check('console and runtime health',!report.consoleErrors.length&&!report.pageErrors.length);report.passed=report.tests>0&&report.screenshots.length===2;
 }catch(error){
 report.failure={kind:error.name,message:String(error.message).slice(0,300)};
 if(lastPage&&lastViewport){
   try{await capture(lastPage,lastViewport,'failure');}
   catch(captureError){report.failure.capture_failed=String(captureError.name);}
 }
}finally{if(browser)await browser.close();if(server)await new Promise(r=>server.close(r));fs.writeFileSync(path.join(out,'manifest.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));process.exitCode=report.passed?0:1;}})();}
