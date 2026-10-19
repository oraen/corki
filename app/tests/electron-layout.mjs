import {_electron as electron} from 'playwright';
import {mkdtemp,writeFile} from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
const home=await mkdtemp(path.join(os.tmpdir(),'corki-layout-'));
await writeFile(path.join(home,'settings.json'),JSON.stringify({default:{provider:'fixture',model:'fixture'},providers:{fixture:{base_url:'http://127.0.0.1:1',api_key:'fixture-not-a-credential',models:{fixture:{context_window:262144}}}}}));
const env={...process.env,CORKI_HOME:home,CORKI_PYTHON:path.resolve('../.venv/bin/python')};delete env.ELECTRON_RUN_AS_NODE;
let app;
try{
 app=await electron.launch({args:[path.resolve('.')],env});
 const page=await app.firstWindow();
 const input=page.getByRole('textbox',{name:'消息',exact:true});
 await page.getByRole('button',{name:'选择模型',exact:true}).filter({hasText:'fixture'}).waitFor();
 for(const [width,height,zoom] of [[1280,800,1],[1440,900,1],[1920,1080,1],[1440,900,1.25]]){
  await app.evaluate(({BrowserWindow},[width,height,zoom])=>{const win=BrowserWindow.getAllWindows()[0];win.setSize(width,height);win.webContents.setZoomFactor(zoom)},[width,height,zoom]);
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const layout=await page.evaluate(()=>{
   const nodes=['.composer','textarea[aria-label="消息"]','button[aria-label="发送消息"]','.directory-bar'].map(selector=>document.querySelector(selector).getBoundingClientRect());
   return {width:innerWidth,height:innerHeight,overflow:document.documentElement.scrollWidth>innerWidth+1,boxes:nodes.map(r=>({left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height}))};
  });
  assert(!layout.overflow,`Window ${width} at ${zoom} overflows horizontally`);
  assert(layout.boxes.every(r=>r.left>=0 && r.right<=layout.width+1 && r.top>=0 && r.bottom<=layout.height+1 && r.width>0 && r.height>0),JSON.stringify(layout));
  const screenshot=await app.evaluate(async({BrowserWindow})=>(await BrowserWindow.getAllWindows()[0].webContents.capturePage()).toPNG().toString('base64'));
  await writeFile(path.join(home,`layout-${width}-${zoom}.png`),Buffer.from(screenshot,'base64'));
 }
 await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].webContents.setZoomFactor(1));
 await input.fill('键盘测试');await input.press('End');
 await page.keyboard.insertText('附加');assert.equal(await input.inputValue(),'键盘测试附加');
 await page.keyboard.press('Meta+z');assert.equal(await input.inputValue(),'键盘测试');
 await page.keyboard.press('Meta+Shift+z');assert.equal(await input.inputValue(),'键盘测试附加');
 await page.keyboard.press('Meta+a');await page.keyboard.press('Meta+c');
 assert.equal(await app.evaluate(({clipboard})=>clipboard.readText()),'键盘测试附加');
 await page.keyboard.press('ArrowRight');await page.keyboard.press('Meta+v');
 assert.equal(await input.inputValue(),'键盘测试附加键盘测试附加');
 await page.keyboard.press('Shift+Enter');assert((await input.inputValue()).endsWith('\n'));
 await page.getByRole('button',{name:'收起侧栏',exact:true}).click();
 await page.getByRole('button',{name:'展开侧栏',exact:true}).waitFor();
 await page.reload();await page.getByRole('button',{name:'展开侧栏',exact:true}).waitFor();
 await page.getByRole('button',{name:'展开侧栏',exact:true}).click();
 await page.keyboard.press('Meta+n');await input.waitFor();
 assert.equal((await page.evaluate(()=>window.corki.request('threads.list'))).threads.length,0);
 assert.equal((await page.evaluate(()=>window.corki.request('status'))).running.length,0);
 console.log('PASS desktop dimensions/125% zoom, native undo/redo/select/copy/paste, newline, sidebar persistence and New Chat shortcut; screenshots:',home);
}finally{if(app)await app.close();}
