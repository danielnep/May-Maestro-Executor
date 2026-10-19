'use strict';
// Trusted, network-isolated Playwright flow runner. Project JSON contains data, never executable JS.
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const {chromium}=require('/opt/visual/node_modules/playwright');
const cfg=JSON.parse(fs.readFileSync('/project/horizon.browser.json','utf8'));
assert.equal(cfg.profile,'interactive-flows-v1');
const views={mobile:{width:390,height:844},desktop:{width:1440,height:960}};
const flows=cfg.flows;
assert.ok(Array.isArray(flows)&&flows.length>=2&&flows.length<=24,'2–24 explicit flows required');
const totalSteps=flows.reduce((n,f)=>n+(Array.isArray(f.steps)?f.steps.length:0),0);
assert.ok(totalSteps>=2&&totalSteps<=160,'2–160 bounded flow steps required');
for(const v of Object.keys(views))assert.ok(flows.some(f=>f.viewport===v),'Missing '+v+' flow');
const evidence='/evidence';fs.mkdirSync(evidence,{recursive:true});
const report={check:'browser-playwright',profile:'interactive-flows-v1',tests:0,passed:false,
 functional_scenarios:0,checks:[],screenshots:[],layout:[],flows:[],failures:[],scope:'Configured real project UI flows; Android native behavior not covered'};
let browser,server,bytes=0;
const safePath=value=>typeof value==='string'&&value.startsWith('/')&&!value.startsWith('//')&&!value.includes('..')&&!value.includes('?')&&!value.includes('#')&&value.length<=120;
function target(page,t){
 if(!t||typeof t!=='object')throw Error('Target locator required');
 if(typeof t.role==='string'&&typeof t.name==='string'&&t.role.length<30&&t.name.length<100)return page.getByRole(t.role,{name:t.name,exact:true});
 if(typeof t.label==='string'&&t.label.length<=120)return page.getByLabel(t.label,{exact:true});
 if(typeof t.test_id==='string'&&/^[\w-]{1,100}$/.test(t.test_id))return page.getByTestId(t.test_id);
 if(typeof t.css==='string'&&t.css.length<=140&&/^[\w.#\-[\]="' :>,()]+$/.test(t.css))return page.locator(t.css);
 throw Error('Unsupported or unsafe locator');
}
function record(name,passed,detail){
 report.tests++;
 report.checks.push({name,passed,...detail});
 if(!passed)report.failures.push({name,...detail});
}
async function capture(page,name,viewport){
 if(report.screenshots.length>=20)throw Error('Screenshot evidence cap exceeded');
 if(!/^[a-z0-9-]{1,74}\.png$/.test(name))throw Error('Unsafe screenshot name');
 const file=path.join(evidence,name);
 await page.screenshot({path:file,fullPage:false,animations:'disabled'});
 const data=fs.readFileSync(file);bytes+=data.length;
 if(bytes>20_000_000||data.length>5_000_000)throw Error('Screenshot budget exceeded');
 report.screenshots.push({file:name,viewport,bytes:data.length,sha256:crypto.createHash('sha256').update(data).digest('hex')});
}
async function stepAction(page,context,step){
 switch(step.action){
 case 'click': await target(page,step.target).click({timeout:5500});break;
 case 'fill':assert.equal(typeof step.value,'string');assert.ok(step.value.length<=300);await target(page,step.target).fill(step.value);break;
 case 'press':assert.ok(typeof step.key==='string'&&step.key.length<=40);await page.keyboard.press(step.key);break;
 case 'expect_visible':await target(page,step.target).waitFor({state:'visible',timeout:5500});break;
 case 'expect_hidden':await target(page,step.target).waitFor({state:'hidden',timeout:5500});break;
 case 'expect_text':assert.ok(typeof step.text==='string'&&step.text.length<=140);await page.getByText(step.text,{exact:step.exact!==false}).first().waitFor({timeout:5500});break;
 case 'expect_class':{
   assert.ok(typeof step.class==='string'&&/^[\w-]{1,70}$/.test(step.class));
   const has=await target(page,step.target).first().evaluate((e,c)=>e.classList.contains(c),step.class);
   assert.equal(has,step.present!==false,'Expected class '+step.class+' present='+String(step.present!==false));break;
 }
 case 'drag':{
   assert.ok(Number.isInteger(step.dx)&&Number.isInteger(step.dy)&&Math.abs(step.dx)<=600&&Math.abs(step.dy)<=800);
   const box=await target(page,step.target).first().boundingBox();
   assert.ok(box&&box.width>=4&&box.height>=4,'Gesture target not visible');
   const x=box.x+box.width/2,y=box.y+Math.min(box.height/2,20);
   await page.mouse.move(x,y);await page.mouse.down();
   try{await page.mouse.move(x+step.dx,y+step.dy,{steps:12});}
   finally{await page.mouse.up();}
   break;
 }
 case 'navigate':assert.ok(safePath(step.path));await page.goto('http://127.0.0.1:4173'+step.path,{waitUntil:'domcontentloaded'});break;
 case 'offline':assert.equal(typeof step.value,'boolean');await context.setOffline(step.value);break;
 default:throw Error('Unsupported browser action');
 }
}
async function inspect(page,view,flow){
 return page.evaluate(({view,flow})=>{
   const root=document.documentElement,issues=[],warnings=[];
   if(root.scrollWidth>innerWidth+1)issues.push({kind:'horizontal_overflow',scroll:root.scrollWidth,width:innerWidth});
   let examined=0;
   for(const e of document.querySelectorAll('button,a[href],input,textarea,[role=dialog],dialog[open]')){
     const r=e.getBoundingClientRect(),s=getComputedStyle(e);
     if(s.display==='none'||s.visibility==='hidden'||!r.width||!r.height)continue;
     if(r.top>=innerHeight||r.bottom<=0)continue;
     if(++examined>140)break;
     const label=(e.getAttribute('aria-label')||e.textContent||e.tagName).trim().slice(0,45);
     if(r.left< -1||r.right>innerWidth+1)issues.push({kind:'horizontal_clip',label});
     if((e.matches('dialog[open],[role=dialog]'))&&r.bottom>innerHeight+1)issues.push({kind:'dialog_bottom_clipped',label,bottom:Math.round(r.bottom),viewport:innerHeight});
     if(innerWidth<600&&e.matches('button')&&(r.width<40||r.height<40))warnings.push({kind:'small_button',label});
   }
   return {name:view,flow,width:innerWidth,height:innerHeight,blocking_issues:issues.slice(0,15),warnings:warnings.slice(0,15),controls_measured:examined,title:document.title};
 },{view,flow});
}
function serve(req,res){
 let name;try{name=decodeURIComponent(new URL(req.url,'http://127.0.0.1').pathname);}catch{res.writeHead(400);return res.end();}
 if(name==='/favicon.ico'){res.writeHead(204);return res.end();}
 if(!safePath(name)){res.writeHead(403);return res.end();}
 const full=path.resolve('/project','.'+(name==='/'?'/index.html':name));
 if(!full.startsWith('/project/')){res.writeHead(403);return res.end();}
 fs.readFile(full,(error,data)=>{
   if(error){res.writeHead(404);return res.end('Not found');}
   res.writeHead(200,{'Content-Type':({'.html':'text/html; charset=utf-8','.css':'text/css','.js':'text/javascript','.mjs':'text/javascript','.json':'application/json','.svg':'image/svg+xml','.png':'image/png','.webmanifest':'application/manifest+json'})[path.extname(full)]||'application/octet-stream','Cache-Control':'no-store'});res.end(data);
 });
}
(async()=>{
 try{
  for(const f of flows){
   assert.ok(typeof f.name==='string'&&/^[a-z0-9-]{1,35}$/.test(f.name));
   assert.ok(Object.hasOwn(views,f.viewport)&&safePath(f.path||'/'));
   assert.ok(Array.isArray(f.steps)&&f.steps.length<=60);
  }
  server=http.createServer(serve);await new Promise(done=>server.listen(4173,'127.0.0.1',done));
  browser=await chromium.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  for(const view of Object.keys(views)){
   let finalPage=null,finalContext=null;
   for(const flow of flows.filter(f=>f.viewport===view)){
    const context=await browser.newContext({viewport:views[view],reducedMotion:'reduce'});
    await context.route('**/*',route=>{let url;try{url=new URL(route.request().url());}catch{return route.abort();}return url.hostname==='127.0.0.1'?route.continue():route.abort();});
    const page=await context.newPage();page.setDefaultTimeout(5500);
    const info={name:flow.name,viewport:view,actions:[],passed:true};
    report.flows.push(info);
    page.on('pageerror',error=>record(view+'/'+flow.name+': pageerror',false,{type:error.name}));
    try{
     await page.goto('http://127.0.0.1:4173'+(flow.path||'/'),{waitUntil:'domcontentloaded'});
     if(flow.ready)await target(page,flow.ready).waitFor({state:'visible',timeout:15000});
     for(const [i,step] of flow.steps.entries()){
      const tag=view+'/'+flow.name+'/' + String(i+1)+' ' + step.action;
      try{
       await stepAction(page,context,step);
       record(tag,true,{});info.actions.push({step:i+1,action:step.action,passed:true});
       if(step.action==='click'||step.action==='drag'||step.action==='fill')report.functional_scenarios++;
       if(step.capture===true)await capture(page,view+'-'+flow.name+'-'+String(i+1)+'.png',view);
      }catch(error){
       info.passed=false;const detail={kind:error.name||'Error',message:String(error.message).slice(0,210)};
       record(tag,false,detail);info.actions.push({step:i+1,action:step.action,passed:false,...detail});
       if(report.screenshots.length<18)try{await capture(page,view+'-'+flow.name+'-failure.png',view);}catch{}
       break;
      }
     }
     const geometry=await inspect(page,view,flow.name);
     info.geometry=geometry;
     record(view+'/'+flow.name+': layout',!geometry.blocking_issues.length,{blocking_issues:geometry.blocking_issues});
     if(geometry.blocking_issues.length)info.passed=false;
     report.layout.push(geometry);
    }catch(error){
      info.passed=false;record(view+'/'+flow.name+': startup',false,{kind:error.name,message:String(error.message).slice(0,210)});
    }
    if(finalContext)await finalContext.close();
    finalContext=context;finalPage=page;
   }
   if(finalPage){
     await capture(finalPage,view+'-rendered.png',view);
     await finalContext.close();
   }
  }
  report.passed=report.tests>=2&&report.functional_scenarios>=2&&report.failures.length===0&&
    report.screenshots.some(x=>x.file==='mobile-rendered.png')&&report.screenshots.some(x=>x.file==='desktop-rendered.png');
 }catch(error){report.failure={kind:error.name,message:String(error.message).slice(0,250)};}
 finally{
  if(browser)await browser.close();
  if(server)await new Promise(done=>server.close(done));
  fs.writeFileSync(path.join(evidence,'manifest.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify(report));process.exitCode=report.passed?0:1;
 }
})();