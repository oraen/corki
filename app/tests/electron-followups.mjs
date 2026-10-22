import { _electron as electron } from 'playwright';
import http from 'node:http';
import { mkdtemp, writeFile } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
const home = await mkdtemp(path.join(os.tmpdir(), 'corki-followups-e2e-'));
const requests = [], releases = [];
const server = http.createServer(async (req, res) => {
  let raw=''; for await (const part of req) raw+=part;
  requests.push(JSON.parse(raw));
  await new Promise(resolve => releases.push(resolve));
  if(res.destroyed) return;
  res.writeHead(200, {'Content-Type':'text/event-stream'});
  res.write('data: '+JSON.stringify({choices:[{index:0,delta:{content:'已处理测试消息。'},finish_reason:null}]})+'\n\n');
  res.end('data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n');
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
await writeFile(path.join(home,'settings.json'),JSON.stringify({default:{provider:'fixture',model:'fixture-model'},providers:{fixture:{base_url:`http://127.0.0.1:${server.address().port}/v1`,api_mode:'chat_completions',api_key:'fixture-not-a-credential',models:{'fixture-model':{context_window:262144}}}},features:{goals:false},skills:{enabled:false},memory:{enabled:false}}));
async function until(check) { const end=Date.now()+20000; while(!check()){if(Date.now()>end)throw Error('Timed out');await new Promise(r=>setTimeout(r,20));} }
let app;
try {
  const env={...process.env,CORKI_HOME:home,CORKI_PYTHON:path.resolve('../.venv/bin/python')};delete env.ELECTRON_RUN_AS_NODE;
  const packaged = process.env.CORKI_TEST_PACKAGED;
  app=await electron.launch({...(packaged ? {executablePath:path.resolve(packaged)} : {}), args:[...(packaged ? [] : [path.resolve('.')]), ...(process.env.CORKI_QUEUE_CDP ? ['--remote-debugging-port='+process.env.CORKI_QUEUE_CDP] : [])],env});
  await app.evaluate(({dialog})=>{dialog.showMessageBox=async()=>({response:1,checkboxChecked:false})});
  const page=await app.firstWindow();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const input=page.getByRole('textbox',{name:'消息',exact:true});
  await page.getByRole('button',{name:'选择模型',exact:true}).filter({hasText:'fixture-model'}).waitFor();
  await input.fill('第一项测试任务');await input.press('Enter');await until(()=>requests.length===1);
  assert.equal(await page.getByRole('button',{name:'追加方式',exact:true}).count(),0);
  assert.equal(await page.locator('.followups').count(),0);
  // Seed delayed messages through the host API to exercise queue management.
  // The normal desktop composer always steers, even with a legacy queue preference.
  const thread = (await page.evaluate(()=>window.corki.request('status'))).running[0];
  async function queue(content) {
    await page.evaluate(async ({thread,content})=>window.corki.request('turn.submit',{
      requestId:crypto.randomUUID(),threadId:thread,content,provider:'fixture',model:'fixture-model',
      reasoning:null,attachments:[],followUpMode:'queue'
    }),{thread,content});
  }
  await queue('等待上一项完成');
  await page.locator('.followup-row').filter({hasText:'等待上一项完成'}).waitFor();
  assert.equal(await input.inputValue(),'');assert.equal(requests.length,1);
  await page.getByRole('button',{name:'编辑',exact:true}).click();
  await page.getByRole('textbox',{name:'编辑待发送消息'}).fill('修改后的排队任务');
  await page.getByRole('button',{name:'保存',exact:true}).click();
  await page.reload();await page.locator('.followup-row').filter({hasText:'修改后的排队任务'}).waitFor();
  await page.getByRole('button',{name:'选择模型',exact:true}).filter({hasText:'fixture-model'}).waitFor();
  await queue('第三项排队任务');
  await page.locator('.followup-row').filter({hasText:'第三项排队任务'}).waitFor();
  if(process.env.CORKI_QUEUE_CDP) {console.log('VISUAL_READY',home);await new Promise(r=>setTimeout(r,45000));}
  releases[0]();await until(()=>requests.length===2);
  assert(JSON.stringify(requests[1].messages).includes('修改后的排队任务'));
  await page.getByRole('button',{name:'停止执行',exact:true}).click();
  await page.getByRole('button',{name:'恢复队列',exact:true}).waitFor();
  await until(()=>releases.length===2);releases[1]();
  await page.getByRole('button',{name:'发送消息',exact:true}).waitFor();
  assert.equal(requests.length,2);
  await page.getByRole('button',{name:'恢复队列',exact:true}).click();await until(()=>requests.length===3);
  await page.evaluate(thread=>window.corki.request('queue.action',{threadId:thread,action:'mode',mode:'queue'}),thread);
  assert.equal(await page.getByRole('button',{name:'追加方式',exact:true}).count(),0);
  await input.fill('补充：只回答一行');await input.press('Enter');
  await page.getByText('补充：只回答一行',{exact:true}).waitFor();
  releases[2]();await until(()=>requests.length===4);
  assert(JSON.stringify(requests[3].messages).includes('补充：只回答一行'));
  releases[3]();await page.getByRole('button',{name:'发送消息',exact:true}).waitFor();
  await page.reload();await page.getByText('补充：只回答一行',{exact:true}).waitFor();
  assert.equal(await page.getByText('补充：只回答一行',{exact:true}).count(),1);
  assert.deepEqual(errors,[]);
  console.log('PASS: queue, edit, reload, stop/resume, live steering, canonical recovery',home);
} catch(error) {
  console.log('FAIL_HOME',home);
  if(app) {const p=await app.firstWindow();console.log((await p.locator('body').innerText()).slice(-3000));await p.screenshot({path:path.join(home,'failure.png')});}
  throw error;
} finally {
  releases.forEach(r=>r());
  if(app) await app.close();
  server.closeAllConnections();await new Promise(r=>server.close(r));
}
