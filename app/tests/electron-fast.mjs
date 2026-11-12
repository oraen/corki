import {_electron as electron} from 'playwright';
import assert from 'node:assert/strict';
import http from 'node:http';
import {mkdtemp,writeFile,readFile} from 'node:fs/promises';
import os from 'node:os';import path from 'node:path';
import {python} from './platform.mjs';import {poll} from './wait.mjs';
const home=await mkdtemp(path.join(os.tmpdir(),'corki-fast-'));
const requests=[];
const server=http.createServer(async(req,res)=>{
 let raw='';for await(const chunk of req)raw+=chunk;requests.push(JSON.parse(raw));
 res.writeHead(200,{'Content-Type':'text/event-stream'});
 res.end('data: '+JSON.stringify({choices:[{index:0,delta:{content:'Fixture reply.'},finish_reason:null}]})+'\n\ndata: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const config=JSON.stringify({default:{provider:'fixture',model:'fixture'},providers:{fixture:{base_url:`http://127.0.0.1:${server.address().port}/v1`,api_mode:'chat_completions',api_key:'fixture',models:{fixture:{context_window:262144,service_tiers:[{id:'priority'}]}}}},skills:{enabled:false},memories:{enabled:false},features:{goals:false}});
await writeFile(path.join(home,'settings.json'),config);
let app;
try{
 const env={...process.env,CORKI_HOME:home,CORKI_PYTHON:python};delete env.ELECTRON_RUN_AS_NODE;
 app=await electron.launch({args:[path.resolve('.')],env});
 await app.evaluate(({dialog})=>{dialog.showMessageBox=async()=>({response:1});});
 const page=await app.firstWindow();await page.getByRole('textbox',{name:'消息',exact:true}).waitFor();
 const settings=page.getByRole('button',{name:'会话设置',exact:true});
 await settings.click();const fast=page.getByRole('checkbox',{name:'fast',exact:true});
 assert(!await fast.isChecked());assert(await fast.isEnabled());await fast.check();
 await page.screenshot({path:path.join(home,'fast-settings.png')});
 await page.getByRole('button',{name:'保存',exact:true}).click();
 await page.getByRole('checkbox',{name:'fast',exact:true}).waitFor({state:'hidden'});
 async function send(text){
  const count=requests.length;await page.getByRole('textbox',{name:'消息',exact:true}).fill(text);
  await page.getByRole('button',{name:'发送消息',exact:true}).click();
  await poll(async()=>requests.length>count && !(await page.evaluate(()=>window.corki.request('status'))).running.length);
 }
 await send('First harmless fixture request');assert.equal(requests[0].service_tier,'priority');
 await settings.click();assert(await fast.isChecked());await fast.uncheck();
 await page.getByRole('button',{name:'保存',exact:true}).click();
 await page.getByRole('checkbox',{name:'fast',exact:true}).waitFor({state:'hidden'});
 await send('Second harmless fixture request');assert(!('service_tier' in requests.at(-1)));
 assert.equal(await readFile(path.join(home,'settings.json'),'utf8'),config);
 await settings.click();assert(!await fast.isChecked());
 console.log('PASS fast checkbox, persisted session override, priority on wire, disabling and unchanged global config',home);
}finally{await app?.close();server.closeAllConnections();await new Promise(r=>server.close(r));}
