// Real Electron interaction against disposable metadata. No model requests.
import {python} from './platform.mjs';
import {_electron as electron} from 'playwright';
import {mkdtemp,mkdir,writeFile,readFile,realpath} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
const home=await mkdtemp(path.join(os.tmpdir(),'corki-workspace-names-'));
let work=path.join(home,'工作目录');await mkdir(work);work=await realpath(work);
await writeFile(path.join(work,'keep.txt'),'unchanged');
await writeFile(path.join(home,'settings.json'),JSON.stringify({default:{provider:'fixture',model:'fixture'},providers:{fixture:{base_url:'http://127.0.0.1:1',api_key:'fixture-key',models:{fixture:{}}}},skills:{enabled:false},memories:{generate:false,use:false},features:{goals:false}}));
execFileSync(python,['-c',`import asyncio,sys
from pathlib import Path
from corki.storage import SQLiteSessionRepository
from corki.protocol.ids import ThreadId
async def seed():
 repo=SQLiteSessionRepository(Path(sys.argv[1])/'sessions/corki.db')
 await repo.create_thread(ThreadId('workspace-fixture'),Path(sys.argv[2]))
asyncio.run(seed())`,home,work]);
let app;
try{
 const env={...process.env,CORKI_HOME:home,CORKI_PYTHON:python};delete env.ELECTRON_RUN_AS_NODE;
 app=await electron.launch({...process.env.CORKI_TEST_PACKAGED
  ? {executablePath:path.resolve(process.env.CORKI_TEST_PACKAGED),args:[]}
  : {args:[path.resolve('.')]},env});
 const page=await app.firstWindow();
 const heading=()=>page.locator(`.history-heading[title="${work}"]`);
 await heading().waitFor();
 async function edit(value){
  await heading().click({button:'right'});
  await page.getByRole('menuitem',{name:'重命名',exact:true}).click();
  await page.getByRole('textbox',{name:'工作目录显示名称'}).fill(value);
 }
 await edit('业务项目');
 assert.equal(await heading().count(),0,'editing must not nest a textbox inside a button');
 const input=page.getByRole('textbox',{name:'工作目录显示名称'});
 await input.dispatchEvent('keydown',{key:'Enter',isComposing:true});
 assert.equal(await input.count(),1,'IME confirmation must not save');
 await input.press('Enter');
 await heading().filter({hasText:'业务项目'}).waitFor();
 assert.equal(await heading().getAttribute('aria-expanded'),'false');
 await page.reload();await heading().filter({hasText:'业务项目'}).waitFor();
 await edit('通过失焦保存');
 await page.locator('.brand').click();
 await heading().filter({hasText:'通过失焦保存'}).waitFor();
 await edit('不应保存');await page.getByRole('textbox',{name:'工作目录显示名称'}).press('Escape');
 await heading().filter({hasText:'通过失焦保存'}).waitFor();
 await edit('   ');await page.getByRole('textbox',{name:'工作目录显示名称'}).press('Enter');
 await heading().filter({hasText:'工作目录'}).waitFor();
 await heading().click({button:'right'});
 await page.screenshot({path:path.join(home,'workspace-menu.png')});
 await page.locator('.brand').click();assert.equal(await page.getByRole('menuitem',{name:'重命名'}).count(),0);
 assert.equal(await readFile(path.join(work,'keep.txt'),'utf8'),'unchanged');
 const listing=await page.evaluate(()=>window.corki.request('threads.list'));
 assert.equal(listing.directories[0].path,work);
 assert.equal(listing.directories[0].name,'工作目录');
 console.log('PASS workspace aliases: context menu, IME, Enter, blur, Escape, reload, reset and unchanged directory');
 console.log('Screenshot:',path.join(home,'workspace-menu.png'));
}finally{await app?.close();}
