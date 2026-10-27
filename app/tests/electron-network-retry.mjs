import { python } from './platform.mjs';
import { _electron as electron } from 'playwright';
import http from 'node:http';
import { mkdtemp, writeFile } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';

const home = await mkdtemp(path.join(os.tmpdir(), 'corki-network-e2e-'));
let mode = 'offline', attempts = 0;
const server = http.createServer(async (req, res) => {
  for await (const part of req) {} // Consume the request without logging private input.
  attempts++;
  if (mode !== 'online') {
    res.writeHead(mode === 'offline' ? 502 : 400, {'Content-Type':'application/json'});
    res.end(JSON.stringify({error:{message:'fixture error',
      ...(mode === 'offline' ? {code:'upstream_connection_failed'} : {})}}));
    return;
  }
  res.writeHead(200, {'Content-Type':'text/event-stream'});
  res.write('data: '+JSON.stringify({choices:[{index:0,delta:{content:'连接恢复成功'},finish_reason:null}]})+'\n\n');
  res.end('data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n');
});
await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
await writeFile(path.join(home,'settings.json'), JSON.stringify({
  default:{provider:'fixture',model:'fixture-model'},
  providers:{fixture:{base_url:`http://127.0.0.1:${server.address().port}/v1`,
    api_mode:'chat_completions',api_key:'fixture-only',models:{'fixture-model':{context_window:262144}}}},
  features:{goals:false},skills:{enabled:false},memory:{enabled:false}
}));
let app;
try {
  const env={...process.env,CORKI_HOME:home,CORKI_PYTHON:python};
  delete env.ELECTRON_RUN_AS_NODE;
  app=await electron.launch({args:[path.resolve('.')],env});
  await app.evaluate(({dialog})=>{dialog.showMessageBox=async()=>({response:1,checkboxChecked:false})});
  const page=await app.firstWindow(), errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  const input=page.getByRole('textbox',{name:'消息',exact:true});
  const banner=page.locator('.model-health-banner').filter({hasText:'网络连接失败，重试中'});
  await input.fill('连接恢复测试'); await input.press('Enter');
  await banner.waitFor();
  assert.equal(await page.getByText('模型调用失败',{exact:true}).count(),0);
  await page.reload(); await banner.waitFor();
  mode='online';
  await page.getByText('连接恢复成功',{exact:true}).waitFor({timeout:20000});
  await page.getByRole('button',{name:'发送消息',exact:true}).waitFor();
  await banner.waitFor({state:'hidden'});
  assert(attempts >= 2);
  mode='unavailable'; const before=attempts;
  await input.fill('不可用模型测试'); await input.press('Enter');
  await page.getByText('模型调用失败',{exact:true}).waitFor();
  await page.getByRole('button',{name:'发送消息',exact:true}).waitFor();
  assert.equal(attempts-before,1);
  assert.equal(await page.locator('.message.error').count(),0);
  await page.reload(); await page.getByText('模型调用失败',{exact:true}).waitFor();
  mode='offline';
  await input.fill('停止重试测试'); await input.press('Enter'); await banner.waitFor();
  await page.getByRole('button',{name:'停止执行',exact:true}).click();
  await page.getByRole('button',{name:'发送消息',exact:true}).waitFor();
  await banner.waitFor({state:'hidden'});
  assert.deepEqual(errors,[]);
  await page.screenshot({path:path.join(home,'network-retry.png')});
  console.log('PASS network recovery, banner reload, terminal 400, history and stop',home);
} finally {
  if(app) await app.close();
  server.closeAllConnections();await new Promise(resolve=>server.close(resolve));
}
