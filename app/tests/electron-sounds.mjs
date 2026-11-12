// One real Electron pass: hidden playback, minimized chat, preferences, renderer recovery.
import {_electron as electron} from 'playwright';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile} from 'node:fs/promises';
import {build} from 'esbuild';
import path from 'node:path';
import os from 'node:os';
import {python} from './platform.mjs';
const home=await mkdtemp(path.join(os.tmpdir(),'corki-sounds-electron-'));
const settings={default:{provider:'fixture',model:'fixture'},providers:{fixture:{base_url:'http://127.0.0.1:1',api_key:'fixture',models:{fixture:{context_window:262144}}}}};
const setSound=enabled=>writeFile(path.join(home,'settings.json'),JSON.stringify({...settings,tui:{notifications:enabled}}));
await setSound(true);
const audioModule=path.join(home,'notification-audio.cjs');
await build({entryPoints:['electron/notification-audio.ts'],outfile:audioModule,bundle:true,platform:'node',format:'cjs',external:['electron']});
const env={...process.env,CORKI_HOME:home,CORKI_PYTHON:python};delete env.ELECTRON_RUN_AS_NODE;
let app;
try{
 app=await electron.launch({args:[path.resolve('.')],env});
 const page=await app.firstWindow();await page.getByRole('textbox',{name:'消息',exact:true}).waitFor();
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const workerReady=app.waitForEvent('window');
 await app.evaluate(async(_,modulePath)=>{
  const {createRequire}=process.getBuiltinModule('node:module');
  const {NotificationAudio}=createRequire(modulePath)(modulePath);
  globalThis.testAudio=new NotificationAudio();globalThis.testAudio.play('complete');
 },audioModule);
 const worker=await workerReady;
 await worker.waitForURL('corki://desktop/notification-audio.html');
 await worker.waitForLoadState();
 assert.equal(await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows().find(w=>w.webContents.getURL().endsWith('/notification-audio.html')).isVisible()),false);
 await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows().find(w=>w.isVisible()).minimize());
 for(const kind of ['complete','attention','error']){
  const result=await worker.evaluate(async kind=>{const start=performance.now();await window.playNotification(kind);return performance.now()-start;},kind);
  assert(result>=400&&result<2000,`${kind} duration ${result}`);
 }
 await setSound(false);
 await page.reload();await page.getByRole('button',{name:'打开管理页面',exact:true}).waitFor();
 assert.equal(await page.getByRole('button',{name:'声音提醒',exact:true}).count(),0);
 assert.equal((await page.evaluate(()=>window.corki.request('notifications.read'))).enabled,false);
 await worker.evaluate(()=>{window.playNotification=()=>new Promise(()=>{});});
 await app.evaluate(()=>globalThis.testAudio.play('complete'));
 // Hung audio worker times out and is destroyed without affecting the chat.
 await worker.waitForEvent('close',{timeout:5000});
 await setSound(true);
 assert.equal((await page.evaluate(()=>window.corki.request('notifications.read'))).enabled,true);
 await app.evaluate(()=>globalThis.testAudio.play('complete'));
 await page.waitForTimeout(1000);
 assert(app.windows().some(p=>p.url().endsWith('/notification-audio.html')));
 assert.deepEqual(errors,[]);
 await app.evaluate(()=>globalThis.testAudio.dispose());
 console.log('PASS hidden/background sounds, durations, persisted mute, reload and audio timeout recovery');
}finally{await app?.close();}
