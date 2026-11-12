import {_electron as electron} from 'playwright';
import assert from 'node:assert/strict';
import http from 'node:http';
import {mkdtemp,writeFile} from 'node:fs/promises';
import os from 'node:os';import path from 'node:path';
import {execFile} from 'node:child_process';import {promisify} from 'node:util';
import {python} from './platform.mjs';import {poll} from './wait.mjs';
const home=await mkdtemp(path.join(os.tmpdir(),'corki-compact-'));
let requests=0;
const server=http.createServer(async(req,res)=>{for await(const _ of req){}requests++;
 await new Promise(r=>setTimeout(r,300));res.writeHead(200,{'Content-Type':'text/event-stream'});
 res.end('data: '+JSON.stringify({choices:[{index:0,delta:{content:'Task summary: the fixture answered the question.'},finish_reason:null}]})+'\n\ndata: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n');});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
await writeFile(path.join(home,'settings.json'),JSON.stringify({default:{provider:'fixture',model:'fixture'},providers:{fixture:{base_url:`http://127.0.0.1:${server.address().port}/v1`,api_mode:'chat_completions',api_key:'fixture',models:{fixture:{context_window:262144}}}},skills:{enabled:false},memories:{enabled:false},features:{goals:false}}));
let app;
try{
 const env={...process.env,CORKI_HOME:home,CORKI_PYTHON:python};delete env.ELECTRON_RUN_AS_NODE;
 const packaged=process.env.CORKI_TEST_PACKAGED;
 app=await electron.launch({...(packaged?{executablePath:path.resolve(packaged)}:{}),args:packaged?[]:[path.resolve('.')],env});
 await app.evaluate(({dialog})=>{dialog.showMessageBox=async()=>({response:1});});
 const page=await app.firstWindow();await page.getByRole('textbox',{name:'消息',exact:true}).waitFor();
 await page.getByRole('button',{name:'会话设置',exact:true}).click();
 assert(await page.getByRole('button',{name:'压缩当前上下文',exact:true}).isDisabled());
 await page.getByRole('button',{name:'取消',exact:true}).click();
 await page.getByRole('textbox',{name:'消息',exact:true}).fill('A harmless fixture question');
 await page.getByRole('button',{name:'发送消息',exact:true}).click();
 await poll(async()=>requests===1 && !(await page.evaluate(()=>window.corki.request('status'))).running.length);
 const threads=(await page.evaluate(()=>window.corki.request('threads.list'))).threads;
 const thread=threads[0].id;
 await promisify(execFile)(python,['-c',`
import asyncio,sys,uuid
from pathlib import Path
from corki.storage.sqlite import SQLiteSessionRepository
from corki.protocol.ids import ThreadId,TurnId
from corki.protocol.items import UserMessageItem,AssistantMessageItem,new_step_id
from corki.sessions.models import TurnRecord,TurnStatus
async def seed():
 repo=SQLiteSessionRepository(Path(sys.argv[1])/'sessions/corki.db');thread=ThreadId(sys.argv[2])
 for i in range(72):
  turn=TurnId(str(uuid.uuid4()));question=UserMessageItem('Fixture question '+str(i),turn)
  answer=AssistantMessageItem('Fixture answer '+str(i),turn,new_step_id())
  await repo.save_turn(TurnRecord(turn,thread,TurnStatus.COMPLETED,question.content,answer.content))
  await repo.append_items(thread,(question,answer))
asyncio.run(seed())
 `,home,thread]);
 const before=await page.evaluate(id=>window.corki.request('threads.read',{threadId:id}),thread);
 assert.equal(before.messages.length,60);

 await page.getByRole('button',{name:'会话设置',exact:true}).click();
 await page.getByRole('button',{name:'压缩当前上下文',exact:true}).click();
 await page.getByText('已开始压缩，可关闭设置查看进度',{exact:true}).waitFor();
 await page.screenshot({path:path.join(home,'compact-settings.png')});
 await page.getByRole('button',{name:'取消',exact:true}).click();
 await poll(async()=>requests>=2 && !(await page.evaluate(()=>window.corki.request('status'))).running.length);
 const info=await page.evaluate(id=>window.corki.request('threads.read',{threadId:id}),thread);
 assert(info.turns.some(t=>t.status==='completed'));
 const receipt=info.receipt;assert.equal(receipt.state,'completed');
 assert.deepEqual(info.messages.map(m=>m.id),before.messages.map(m=>m.id),'Compaction must not change the visible page');
 assert.equal(info.next,before.next);
 const visibleIds=[];let cursor;
 do{const part=await page.evaluate(p=>window.corki.request('threads.read',p),{threadId:thread,...(cursor!==undefined?{before:cursor}:{})});visibleIds.push(...part.messages.map(m=>m.id));cursor=part.next;}while(cursor!==null);
 assert.equal(visibleIds.length,146);
 assert.equal(new Set(visibleIds).size,146);
 await page.reload();await page.getByRole('textbox',{name:'消息',exact:true}).waitFor();
 const restored=await page.evaluate(id=>window.corki.request('threads.read',{threadId:id}),thread);
 assert.deepEqual(restored.messages.map(m=>m.id),before.messages.map(m=>m.id));
 console.log('PASS actual manual compaction model call, completed receipt, new-chat protection, 146 independent history records, stable paging and reload',home);
}finally{await app?.close();server.closeAllConnections();await new Promise(r=>server.close(r));}
