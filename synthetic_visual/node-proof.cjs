'use strict';
// Isolated untrusted project: no host credentials, no network and only bounded tests.
const {spawnSync}=require('node:child_process');
const fs=require('node:fs');
const root='/project/tests';
const files=fs.existsSync(root)
  ?fs.readdirSync(root).filter(name=>/\.test\.(?:cjs|mjs|js)$/.test(name)).sort().map(name=>root+'/'+name)
  :[];
if(!files.length){
  console.log(JSON.stringify({check:'node-test',passed:false,tests:0,reason:'No Node tests discovered'}));
  process.exit(1);
}
const result=spawnSync(process.execPath,['--test','--test-reporter=tap',...files],{
  encoding:'utf8',timeout:90000,maxBuffer:64000,
  env:{PATH:process.env.PATH,HOME:'/tmp'}
});
const output=String(result.stdout||'')+String(result.stderr||'');
const count=Number((output.match(/# tests (\d+)/)||[])[1]||0);
const passed=result.status===0&&count>0&&!result.error;
console.log(JSON.stringify({check:'node-test',passed,tests:count,exit_code:result.status,
  output:output.slice(-12000)}));
process.exit(passed?0:1);
