import {poll} from './wait.mjs';
import {_electron as electron} from 'playwright';
import http from 'node:http';
import {mkdtemp,writeFile} from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
const home=await mkdtemp(path.join(os.tmpdir(),'corki-edge-'));
let calls=0;
const server=http.createServer(async(req,res)=>{
 let raw='';for await(const chunk of req)raw+=chunk;const body=JSON.parse(raw);calls++;
 if(JSON.stringify(body.messages).includes('fixture failure')){res.writeHead(400,{'Content-Type':'application/json'});res.end(JSON.stringify({error:{message:'fixture rejected model request'}}));return;}
 res.writeHead(200,{'Content-Type':'text/event-stream'});
 res.end('data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n');
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
let app;
try{
 const env={...process.env,CORKI_HOME:home,CORKI_PYTHON:path.resolve('../.venv/bin/python')};delete env.ELECTRON_RUN_AS_NODE;
 app=await electron.launch({args:[path.resolve('.')],env});
 await app.evaluate(({dialog})=>{dialog.showMessageBox=async()=>({response:1})});
 const page=await app.firstWindow();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.locator('.banner').filter({hasText:'尚未配置模型'}).waitFor();
 const input=page.getByRole('textbox',{name:'消息',exact:true});await input.fill('empty fixture');
 assert(await page.getByRole('button',{name:'发送消息'}).isDisabled());assert.equal(calls,0);
 await writeFile(path.join(home,'settings.json'),'{invalid');
 await page.getByRole('button',{name:'刷新配置'}).click();
 await page.locator('.banner').waitFor();assert(await page.getByRole('button',{name:'发送消息'}).isDisabled());
 const config={default:{provider:'fixture',model:'fixture'},providers:{fixture:{base_url:`http://127.0.0.1:${server.address().port}/v1`,api_key:'fixture-not-a-credential',api_mode:'chat_completions',models:{fixture:{context_window:262144}}}},skills:{enabled:false},features:{goals:false,plugins:false},memories:{generate:false,use:false}};
 await writeFile(path.join(home,'settings.json'),JSON.stringify(config));
 await page.getByRole('button',{name:'刷新配置'}).click();
 await page.getByRole('button',{name:'选择供应商',exact:true}).click();await page.getByRole('option',{name:'fixture',exact:true}).click();
 await page.getByRole('button',{name:'发送消息'}).click();
 await poll(()=>page.evaluate(async()=>{const result=await window.corki.request('threads.list');if(!result.threads.length)return false;const state=await window.corki.request('threads.read',{threadId:result.threads[0].id});return !!state.turns.length && ['completed','failed','cancelled'].includes(state.turns[0].status) && !state.running;}));
 assert.equal(calls,1);assert.equal(await page.locator('.message.assistant').count(),0);
 const thread=(await page.evaluate(()=>window.corki.request('threads.list'))).threads[0].id;
 const snapshot=await page.evaluate(threadId=>window.corki.request('threads.read',{threadId}),thread);
 assert.equal(snapshot.turns[0].status,'completed');
 await page.locator('.new-chat').click();await input.fill('fixture failure');await page.getByRole('button',{name:'发送消息'}).click();
 await page.locator('.message-error').filter({hasText:'fixture rejected model request'}).waitFor({timeout:20000});
 assert.equal(calls,2);assert.equal(await page.locator('.message.user').count(),1);
 assert.deepEqual(errors,[]);
 console.log('PASS native missing/malformed config disables send without requests, corrected config works, empty model reply completes without fabricated answer, HTTP failure preserves user message and explicit error');
}catch(error){if(app)console.error((await (await app.firstWindow()).locator('body').innerText()).slice(-1500));throw error}finally{if(app)await app.close();server.closeAllConnections();server.close();console.log('Disposable test home:',home)}
