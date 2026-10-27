import { python } from './platform.mjs';
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
  const env={...process.env,CORKI_HOME:home,CORKI_PYTHON:python};delete env.ELECTRON_RUN_AS_NODE;
  const packaged = process.env.CORKI_TEST_PACKAGED;
  app=await electron.launch({...(packaged ? {executablePath:path.resolve(packaged)} : {}), args:[...(packaged ? [] : [path.resolve('.')]), ...(process.env.CORKI_QUEUE_CDP ? ['--remote-debugging-port='+process.env.CORKI_QUEUE_CDP] : [])],env});
  await app.evaluate(({dialog})=>{dialog.showMessageBox=async()=>({response:1,checkboxChecked:false})});
  const page=await app.firstWindow();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const input=page.getByRole('textbox',{name:'消息',exact:true});
  await page.getByRole('button',{name:'选择模型',exact:true}).filter({hasText:'fixture-model'}).waitFor();
  await input.fill('第一项测试任务');await input.press('Enter');await until(()=>requests.length===1);
  assert.equal(await page.getByRole('button',{name:'追加方式',exact:true}).count(),0);
  assert.equal(await page.locator('.followups').count(),0);
  // Deferred inputs are ordinary chat bubbles; no user-facing queue controls.
  const thread = (await page.evaluate(()=>window.corki.request('status'))).running[0];
  async function queue(content) {
    await page.evaluate(async ({thread,content})=>window.corki.request('turn.submit',{
      requestId:crypto.randomUUID(),threadId:thread,content,provider:'fixture',model:'fixture-model',
      reasoning:null,attachments:[],followUpMode:'queue'
    }),{thread,content});
  }
  async function noQueueUI() {
    assert.equal(await page.locator('.followups,.followup-row').count(),0);
    assert.equal(await page.getByText(/待发送 ·/).count(),0);
    assert.equal(await page.getByRole('button',{name:'立即补充',exact:true}).count(),0);
    assert.equal(await page.getByRole('button',{name:'恢复队列',exact:true}).count(),0);
  }
  // Even legacy queue preferences must now supplement the active task.
  await queue('补充当前任务');
  await page.locator('.user-text').filter({hasText:'补充当前任务'}).waitFor();
  assert.equal(await page.locator('.pending-input').count(),0);
  await noQueueUI();
  assert.equal(requests.length,1);
  await page.reload();
  await page.locator('.user-text').filter({hasText:'补充当前任务'}).waitFor();
  await noQueueUI();
  releases[0]();await until(()=>requests.length===2);
  assert(JSON.stringify(requests[1].messages).includes('补充当前任务'));
  assert.equal(await page.locator('.user-text').filter({hasText:'补充当前任务'}).count(),1);
  await queue('停止后保留的追问');
  await page.locator('.user-text').filter({hasText:'停止后保留的追问'}).waitFor();
  await page.getByRole('button',{name:'停止执行',exact:true}).click();
  await page.getByRole('button',{name:'发送消息',exact:true}).waitFor();
  releases[1]();
  await noQueueUI();
  // A fresh explicit send is never blocked by the previous stopped input.
  await input.fill('停止后发送新消息');await input.press('Enter');await until(()=>requests.length===3);
  assert(JSON.stringify(requests[2].messages).includes('停止后发送新消息'));
  assert(!JSON.stringify(requests[2].messages).includes('停止后保留的追问'));
  await input.fill('补充：只回答一行');await input.press('Enter');
  await page.locator('.user-text').filter({hasText:'补充：只回答一行'}).waitFor();
  await noQueueUI();
  releases[2]();await until(()=>requests.length===4);
  assert(JSON.stringify(requests[3].messages).includes('补充：只回答一行'));
  releases[3]();await page.getByRole('button',{name:'发送消息',exact:true}).waitFor();
  await page.reload();await page.locator('.user-text').filter({hasText:'补充：只回答一行'}).waitFor();
  assert.equal(await page.locator('.user-text').filter({hasText:'补充：只回答一行'}).count(),1);
  const questions=await page.locator('.user-text').allTextContents();
  assert(questions.indexOf('停止后保留的追问') < questions.indexOf('停止后发送新消息'),
    'An unsent older question must keep its position before later questions');
  await noQueueUI();assert.deepEqual(errors,[]);
  await page.screenshot({path:path.join(home,'hidden-followups.png')});
  console.log('PASS: steering only, legacy queue preference ignored, reload, stop/new input, no duplicates',home);
} catch(error) {
  console.log('FAIL_HOME',home);
  if(app) {const p=await app.firstWindow();console.log((await p.locator('body').innerText()).slice(-3000));await p.screenshot({path:path.join(home,'failure.png')});}
  throw error;
} finally {
  releases.forEach(r=>r());
  if(app) await app.close();
  server.closeAllConnections();await new Promise(r=>server.close(r));
}
