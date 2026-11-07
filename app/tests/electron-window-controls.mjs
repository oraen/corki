// Real native actions, isolated settings and no model requests.
import {_electron as electron} from 'playwright';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile} from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {python} from './platform.mjs';

const home=await mkdtemp(path.join(os.tmpdir(),'corki-window-controls-'));
await writeFile(path.join(home,'settings.json'),JSON.stringify({default:{provider:'fixture',model:'fixture'},providers:{fixture:{base_url:'http://127.0.0.1:1',api_key:'fixture',models:{fixture:{context_window:262144}}}}}));
const env={...process.env,CORKI_HOME:home,CORKI_PYTHON:python};delete env.ELECTRON_RUN_AS_NODE;
let app;
try{
  app=await electron.launch({args:[path.resolve('.')],env});
  const page=await app.firstWindow();
  await page.getByRole('textbox',{name:'消息',exact:true}).waitFor();
  if(process.platform!=='win32'){
    assert.equal(await page.locator('.window-controls').count(),0);
    console.log('PASS native non-Windows window has no custom controls');
  }else{
    const layout=await page.evaluate(()=>{
      const restart=document.querySelector('.app-restart').getBoundingClientRect(),controls=document.querySelector('.window-controls').getBoundingClientRect();
      return {restartRight:restart.right,controlsLeft:controls.left,right:controls.right,width:innerWidth,
        barDrag:getComputedStyle(document.querySelector('.topbar')).webkitAppRegion,
        controlsDrag:getComputedStyle(document.querySelector('.window-controls')).webkitAppRegion};
    });
    assert(layout.controlsLeft>=layout.restartRight && Math.abs(layout.right-layout.width)<1);
    assert.equal(layout.barDrag,'drag');assert.equal(layout.controlsDrag,'no-drag');
    const frame=await app.evaluate(({BrowserWindow})=>{const win=BrowserWindow.getAllWindows()[0];return {outer:win.getBounds(),content:win.getContentBounds()};});
    assert(frame.content.y-frame.outer.y<16,'Native title bar must not occupy vertical space');
    await page.getByRole('button',{name:'最大化',exact:true}).click();
    await page.getByRole('button',{name:'还原窗口',exact:true}).waitFor();
    assert(await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].isMaximized()));
    await page.getByRole('button',{name:'还原窗口',exact:true}).click();
    await page.getByRole('button',{name:'最大化',exact:true}).waitFor();
    assert.equal(await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].isMaximized()),false);
    // Native maximize (e.g. double-click / Win+Up) must also update the renderer.
    await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].maximize());
    await page.getByRole('button',{name:'还原窗口',exact:true}).waitFor();
    await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].unmaximize());
    await page.getByRole('button',{name:'最大化',exact:true}).waitFor();
    await page.getByRole('button',{name:'最小化',exact:true}).click();
    assert(await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].isMinimized()));
    await app.evaluate(({BrowserWindow})=>{const win=BrowserWindow.getAllWindows()[0];win.restore();win.show();});
    const rejected=await page.evaluate(async()=>{
      try{await window.corki.windowControl(['minimize']);return false;}catch{return true;}
    });
    assert(rejected);
    await page.screenshot({path:path.join(home,'windows-controls.png')});
    const closed=app.waitForEvent('close');
    await page.getByRole('button',{name:'关闭窗口',exact:true}).click();await closed;
    app=null;
    console.log('PASS Windows frameless header, control placement, native minimize/maximize/restore/close, OS-state updates and action validation; screenshot:',home);
  }
}finally{if(app)await app.close();}
