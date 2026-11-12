import {_electron as electron} from 'playwright';
import assert from 'node:assert/strict';
import http from 'node:http';
import {PNG} from 'pngjs';
import {randomFillSync} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {mkdtemp,writeFile} from 'node:fs/promises';
import os from 'node:os';import path from 'node:path';
import {python} from './platform.mjs';import {poll} from './wait.mjs';
const home=await mkdtemp(path.join(os.tmpdir(),'corki-paste-images-'));
let received;
const server=http.createServer(async(req,res)=>{
 let raw='';for await(const part of req)raw+=part;received=JSON.parse(raw);
 res.writeHead(200,{'Content-Type':'text/event-stream'});
 res.end('data: {"choices":[{"index":0,"delta":{"content":"Fixture received the image."},"finish_reason":null}]}\n\ndata: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n');
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
await writeFile(path.join(home,'settings.json'),JSON.stringify({default:{provider:'fixture',model:'fixture'},providers:{fixture:{base_url:`http://127.0.0.1:${server.address().port}/v1`,api_key:'fixture',models:{fixture:{context_window:262144}}}},skills:{enabled:false},memories:{enabled:false},features:{goals:false}}));
let app;
try{
 const env={...process.env,CORKI_HOME:home,CORKI_PYTHON:python};delete env.ELECTRON_RUN_AS_NODE;
 app=await electron.launch({args:[path.resolve('.')],env});
 await app.evaluate(({dialog})=>{dialog.showMessageBox=async()=>({response:1});});
 const page=await app.firstWindow();page.setDefaultTimeout(10000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const input=page.getByRole('textbox',{name:'消息',exact:true});await input.waitFor();
 await app.evaluate(({clipboard})=>clipboard.writeText('普通文字粘贴'));
 await input.focus();await input.press(process.platform==='darwin'?'Meta+v':'Control+v');
 await poll(async()=>await input.inputValue()==='普通文字粘贴');
 const artwork=new PNG({width:900,height:700});randomFillSync(artwork.data);
 const png=PNG.sync.write(artwork).toString('base64');assert(Buffer.from(png,'base64').length>1_000_000);
 if(process.platform==='darwin'){
  const image=path.join(home,'clipboard.png');await writeFile(image,Buffer.from(png,'base64'));
  execFileSync('osascript',['-e','on run argv\nset the clipboard to (read POSIX file (item 1 of argv) as «class PNGf»)\nend run',image]);
 }else await app.evaluate(({clipboard,ClipboardItem},png)=>clipboard.write([new ClipboardItem({'image/png':new Blob([Buffer.from(png,'base64')],{type:'image/png'})})]),png);
 await input.press(process.platform==='darwin'?'Meta+v':'Control+v');

 await page.locator('.attachment img').waitFor();assert.equal(await input.inputValue(),'普通文字粘贴');
 const imageName=await page.locator('.attachment-name').innerText();
 await page.getByRole('button',{name:imageName,exact:true}).click();await page.locator('.image-preview').waitFor();
 await page.getByRole('button',{name:'关闭',exact:true}).click();
 await page.getByRole('button',{name:`移除 ${imageName}`,exact:true}).click();assert.equal(await page.locator('.attachment').count(),0);
 if(process.platform==='darwin'){
  await app.evaluate(({dialog},file)=>{dialog.showOpenDialog=async()=>({canceled:false,filePaths:[file]})},path.join(home,'clipboard.png'));
  await page.getByRole('button',{name:'添加附件',exact:true}).click();
  await page.getByRole('button',{name:'移除 clipboard.png',exact:true}).waitFor();
  await page.getByRole('button',{name:'移除 clipboard.png',exact:true}).click();
 }
 await input.focus();await input.press(process.platform==='darwin'?'Meta+v':'Control+v');
 await page.locator('.attachment img').waitFor();
 await poll(async()=>Boolean((await page.evaluate(()=>window.corki.request('state.read',{key:'draft:new'})))?.attachments[0]?.assetId));
 await page.reload();await page.locator('.attachment img').waitFor();
 await page.screenshot({path:path.join(home,'pasted-image.png')});
 // The bounded image-only IPC must reject invalid transport values.
 assert(await page.evaluate(async()=>{try{await window.corki.importImages([{name:'bad',mime:'text/html',bytes:new ArrayBuffer(1)}]);return false}catch{return true}}));
 await page.getByRole('button',{name:'发送消息',exact:true}).click();
 await poll(async()=>received && !(await page.evaluate(()=>window.corki.request('status'))).running.length);
 const content=received.messages.findLast(m=>m.role==='user').content;
 assert(content.some(p=>p.type==='image_url'&&p.image_url.url.startsWith('data:image/png;base64,')));
 assert(content.some(p=>p.type==='text'&&p.text.includes('普通文字粘贴')));assert.deepEqual(errors,[]);
 console.log('PASS native clipboard image paste, text paste, preview, remove, reload, validation and real model image payload',home);
}catch(error){if(app)console.error((await (await app.firstWindow()).locator('body').innerText()).slice(-1600));throw error;}finally{await app?.close();server.closeAllConnections();await new Promise(r=>server.close(r));}
