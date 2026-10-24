import { python } from './platform.mjs';
import {poll} from './wait.mjs';
import {_electron as electron} from 'playwright';
import electronBinary from 'electron';
import {spawn} from 'node:child_process';
import {mkdtemp,mkdir,writeFile,readFile} from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
const home=await mkdtemp(path.join(os.tmpdir(),'corki-lifecycle-'));
const settings=JSON.stringify({default:{provider:'fixture',model:'fixture'},providers:{fixture:{base_url:'http://127.0.0.1:1',api_key:'fixture-not-a-credential',models:{fixture:{context_window:262144}}}}});
await writeFile(path.join(home,'settings.json'),settings);
await mkdir(path.join(home,'app'));
await writeFile(path.join(home,'app/window.json'),JSON.stringify({bounds:{x:-100000,y:-100000,width:'corrupt',height:99999}}));
const env={...process.env,CORKI_HOME:home,CORKI_PYTHON:python};
delete env.ELECTRON_RUN_AS_NODE;
let app,second;
const startupTimeout=process.platform==='win32'?120000:30000;
try{
 app=await electron.launch({args:[path.resolve('.')],env});
 let page=await app.firstWindow();
 page.setDefaultTimeout(startupTimeout);
 await page.getByRole('textbox',{name:'消息',exact:true}).fill('重启后保留的草稿');
 await poll(()=>page.evaluate(async()=>{
   const draft=await window.corki.request('state.read',{key:'draft:new'});
   return draft?.content==='重启后保留的草稿';
 }));
 const visible=await app.evaluate(({BrowserWindow,screen})=>{
   const bounds=BrowserWindow.getAllWindows()[0].getBounds();
   const area=screen.getDisplayMatching(bounds).workArea;
   return bounds.x>=area.x && bounds.y>=area.y && bounds.x+bounds.width<=area.x+area.width && bounds.y+bounds.height<=area.y+area.height;
 });
 assert.equal(visible,true,'Invalid and off-screen stored bounds must be restored onto the current display');
 second=spawn(electronBinary,[path.resolve('.')],{env,stdio:'ignore'});
 const exit=await new Promise((resolve,reject)=>{
   const timer=setTimeout(()=>reject(new Error('Second application instance did not exit')),process.platform==='win32'?startupTimeout:10000);
   second.once('exit',code=>{clearTimeout(timer);resolve(code)});
   second.once('error',error=>{clearTimeout(timer);reject(error)});
 });
 assert.equal(exit,0);second=null;
 assert.equal(await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows().length),1);
 await app.close();app=null;
 app=await electron.launch({args:[path.resolve('.')],env});
 page=await app.firstWindow();
 page.setDefaultTimeout(startupTimeout);
 await page.waitForFunction(()=>document.querySelector('textarea[aria-label="消息"]')?.value==='重启后保留的草稿');
 assert.equal(await readFile(path.join(home,'settings.json'),'utf8'),settings);
 console.log('PASS single instance, full application restart/draft recovery, corrupt/off-screen bounds and unchanged settings');
}finally{
 if(second)second.kill();
 if(app)await app.close();
 console.log('Disposable test home:',home);
}
