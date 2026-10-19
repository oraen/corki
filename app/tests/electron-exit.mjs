import {_electron as electron} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdtemp,mkdir,writeFile} from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
const home=await mkdtemp(path.join(os.tmpdir(),'corki-owned-process-'));
const python=path.resolve('../.venv/bin/python');
const cwd=path.join(home,'main/default');await mkdir(cwd,{recursive:true});
const script=path.join(cwd,'fixture.py');
await writeFile(script,"import os,sys,time,subprocess,json\nfrom pathlib import Path\nchild=subprocess.Popen([sys.executable,'-c','import time;time.sleep(300)'])\nprint('OWNED_PIDS:'+json.dumps([os.getpid(),child.pid]),flush=True)\nprint('OWNED_PROCESS_READY',flush=True)\ntime.sleep(300)\n");
const quote=s=>"'"+s.replaceAll("'","'\\''")+"'";
let modelWaiting=false;
const server=http.createServer(async(req,res)=>{
 let raw='';for await(const b of req)raw+=b;
 const body=JSON.parse(raw);
 res.writeHead(200,{'Content-Type':'text/event-stream'});
 if(body.messages.some(m=>m.role==='tool')){
  const output=body.messages.filter(m=>m.role==='tool').map(m=>String(m.content)).join('\n');
  const match=output.match(/OWNED_PIDS:\[(\d+), ?(\d+)\]/);if(match)owned=[Number(match[1]),Number(match[2])];
  modelWaiting=true;res.write(': fixture waiting\n\n');return;
 }
 const name=body.tools.map(t=>t.function?.name||t.name).find(n=>n.endsWith('exec_command'));
 if(!name){res.end();return;}
 const delta={tool_calls:[{index:0,id:'owned-call',type:'function',function:{name,arguments:JSON.stringify({cmd:quote(python)+' '+quote(script),login:false,yield_time_ms:1000})}}]};
 res.end(`data: ${JSON.stringify({choices:[{index:0,delta,finish_reason:null}]})}\n\ndata: {"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n\ndata: [DONE]\n\n`);
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
await writeFile(path.join(home,'settings.json'),JSON.stringify({default:{provider:'fixture',model:'fixture'},providers:{fixture:{base_url:`http://127.0.0.1:${server.address().port}/v1`,api_key:'fixture-not-a-credential',api_mode:'chat_completions',models:{fixture:{context_window:262144}}}},approval_policy:'never',tools:{mode:'direct',search_mode:'disabled'},skills:{enabled:false},features:{goals:false,plugins:false},memories:{generate:false,use:false}}));
const alive=pid=>{try{process.kill(pid,0);return true}catch{return false}};
const wait=async(check,label)=>{for(let i=0;i<100;i++){if(await check())return;await new Promise(r=>setTimeout(r,100));}throw Error(label)};
const unrelated=spawn(python,['-c','import time;time.sleep(300)'],{stdio:'ignore'});
let app,owned=[];
try{
 const env={...process.env,CORKI_HOME:home,CORKI_PYTHON:python};delete env.ELECTRON_RUN_AS_NODE;
 app=await electron.launch({args:[path.resolve('.')],env});
 const page=await app.firstWindow();
 await page.getByRole('textbox',{name:'消息',exact:true}).fill('Run isolated process fixture');
 await page.getByRole('button',{name:'发送消息'}).click();
 await wait(()=>owned.length===2,'Fixture process did not start');
 await wait(()=>modelWaiting,'Agent did not receive background execution output');
 assert(owned.every(alive));
 await app.evaluate(({app,dialog})=>{globalThis.fixturePrompted=false;dialog.showMessageBox=async()=>{globalThis.fixturePrompted=true;return {response:0}};app.quit()});
 await wait(()=>app.evaluate(()=>globalThis.fixturePrompted),'Quit confirmation not shown');
 assert(owned.every(alive),'Declined quit must leave Agent tools running');
 assert.equal((await page.evaluate(()=>window.corki.request('status'))).running.length,1);
 await app.evaluate(({dialog})=>{dialog.showMessageBox=async()=>({response:1})});
 await app.close();app=null;
 await wait(()=>owned.every(pid=>!alive(pid)),'Owned execution descendants survived app quit');
 assert(alive(unrelated.pid),'Application killed an unrelated process');
 console.log('PASS declined quit retains active task; accepted quit cleans real exec_command process and descendant, preserving unrelated process');
}finally{
 if(app){await app.evaluate(({dialog})=>{dialog.showMessageBox=async()=>({response:1})}).catch(()=>{});await app.close();}
 for(const pid of owned)if(alive(pid))process.kill(pid,'SIGKILL');
 unrelated.kill();server.closeAllConnections();server.close();
 console.log('Disposable test home:',home);
}
