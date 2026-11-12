// Each exposed setting is saved, restored and reset against the real desktop host.
import {_electron as electron} from 'playwright';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile} from 'node:fs/promises';
import path from 'node:path';import os from 'node:os';
import {python} from './platform.mjs';
const home=await mkdtemp(path.join(os.tmpdir(),'corki-session-settings-'));
const model=(window,reasoning=true)=>({context_window:window,max_context_window:window*2,
 supported_reasoning_levels:reasoning?[{effort:'low'},{effort:'high'}]:[]});
const config={default:{provider:'alpha',model:'small'},providers:Object.fromEntries(Object.entries({
 alpha:{small:model(131072),large:model(262144),plain:model(65536,false)},beta:{remote:model(65536)}
}).map(([id,models])=>[id,{base_url:'http://127.0.0.1:1',api_key:'fixture',models}]))};
await writeFile(path.join(home,'settings.json'),JSON.stringify(config));
const original=await readFile(path.join(home,'settings.json'),'utf8');
const env={...process.env,CORKI_HOME:home,CORKI_PYTHON:python};delete env.ELECTRON_RUN_AS_NODE;
let app;
try{
 app=await electron.launch({args:[path.resolve('.')],env});
 let page=await app.firstWindow();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.getByRole('textbox',{name:'消息',exact:true}).waitFor();
 assert.equal(await page.locator('.composer-toolbar select,.model-controls').count(),0);
 const choose=async(dialog,label,value)=>{await dialog.getByRole('button',{name:label,exact:true}).click();await dialog.getByRole('listbox',{name:label,exact:true}).locator(`[role=option][data-value=${JSON.stringify(value)}]`).click();};
 const open=async()=>{await page.getByRole('button',{name:'会话设置',exact:true}).click();await page.getByRole('dialog').waitFor();return page.getByRole('dialog');};
 let dialog=await open();
 assert.equal(await dialog.getByLabel('供应商',{exact:true}).getAttribute('data-value'),'');
 assert.equal(await dialog.getByLabel('供应商',{exact:true}).locator('.picker-value-badge').innerText(),'alpha');
 assert.equal(await dialog.getByLabel('模型',{exact:true}).locator('.picker-value-badge').innerText(),'small');
 assert.equal(await dialog.getByLabel('操作权限',{exact:true}).locator('.picker-value-badge').innerText(),'只读');
 await page.screenshot({path:path.join(home,'session-settings-defaults.png')});
 assert.equal(await dialog.locator('.session-sound-policy').count(),0); // Sound belongs to global settings only.
 assert.equal(await dialog.getByRole('switch').count(),0);
 await choose(dialog,'供应商','beta');
 await choose(dialog,'模型','remote');
 await choose(dialog,'推理等级','high');
 await dialog.getByLabel('上下文窗口',{exact:true}).fill('64');
 await choose(dialog,'供应商','alpha');
 assert.equal(await dialog.getByLabel('模型',{exact:true}).getAttribute('data-value'),'');
 assert.equal(await dialog.getByLabel('推理等级',{exact:true}).getAttribute('data-value'),'');
 assert.equal(await dialog.getByLabel('上下文窗口',{exact:true}).inputValue(),'');
 await choose(dialog,'模型','plain');
 assert.equal(await dialog.getByLabel('推理等级',{exact:true}).isDisabled(),true);
 await choose(dialog,'模型','large');
 await choose(dialog,'推理等级','high');
 await dialog.getByLabel('上下文窗口',{exact:true}).fill('128');
 assert.equal(await dialog.getByRole('button',{name:'工作模式',exact:true}).count(),0);
 assert.equal(await dialog.getByRole('button',{name:'Skills',exact:true}).count(),0);
 await dialog.getByRole('checkbox',{name:'仅规划',exact:true}).check();
 const modes=await page.evaluate(async()=>(await window.corki.request('config.read',{})).permissionModes);
 assert(modes.includes('full'));
 for(const permission of modes) await choose(dialog,'操作权限',permission);
 await choose(dialog,'操作权限','full');
 await choose(dialog,'长期记忆','true');
 const screenshot=path.join(home,'session-settings.png');await page.screenshot({path:screenshot});
 await dialog.getByRole('button',{name:'保存',exact:true}).click();await dialog.waitFor({state:'hidden'});
 let stored=await page.evaluate(()=>window.corki.request('state.read',{key:'draft:new'}));
 assert.deepEqual(stored.sessionSettings,{provider:'alpha',model:'large',reasoning:'high',mode:'plan',permissions:'full',memories:true,contextWindow:131072});
 assert.equal(stored.model,'large');
 await page.reload();await page.getByRole('textbox',{name:'消息',exact:true}).waitFor();
 dialog=await open();assert.equal(await dialog.getByLabel('模型',{exact:true}).getAttribute('data-value'),'large');
 assert.equal(await dialog.getByLabel('上下文窗口',{exact:true}).inputValue(),'128');
 assert(await dialog.getByRole('checkbox',{name:'仅规划',exact:true}).isChecked());
 assert.equal(await dialog.getByLabel('操作权限',{exact:true}).getAttribute('data-value'),'full');
 assert.equal(await dialog.getByLabel('长期记忆',{exact:true}).getAttribute('data-value'),'true');
 await choose(dialog,'供应商','beta');
 await dialog.getByRole('button',{name:'取消',exact:true}).click();
 stored=await page.evaluate(()=>window.corki.request('state.read',{key:'draft:new'}));assert.equal(stored.sessionSettings.provider,'alpha');
 dialog=await open();await dialog.getByRole('checkbox',{name:'仅规划',exact:true}).uncheck();
 await dialog.getByRole('button',{name:'保存',exact:true}).click();await dialog.waitFor({state:'hidden'});
 stored=await page.evaluate(()=>window.corki.request('state.read',{key:'draft:new'}));assert.equal(stored.sessionSettings.mode,'default');
 dialog=await open();assert(!await dialog.getByRole('checkbox',{name:'仅规划',exact:true}).isChecked());
 await dialog.getByRole('button',{name:'恢复全局设置',exact:true}).click();
 await dialog.getByRole('button',{name:'保存',exact:true}).click();await dialog.waitFor({state:'hidden'});
 stored=await page.evaluate(()=>window.corki.request('state.read',{key:'draft:new'}));assert.deepEqual(stored.sessionSettings,{});assert.equal(stored.model,'small');
 assert.equal(await readFile(path.join(home,'settings.json'),'utf8'),original);
 assert.deepEqual(errors,[]);
 console.log('PASS every setting, supplier/model dependencies, persisted reload, cancel, reset and global isolation');
 console.log('Screenshot: '+screenshot);
}finally{await app?.close();}
