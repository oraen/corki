// Browser QA through the local CDP workflow from the user's read_chrome skill.
// Use a disposable manager home for --write; never mutate the user's workspace.
import fs from 'node:fs';
import assert from 'node:assert/strict';
const debug = process.env.CHROME_DEBUG_PORT || '9225';
const base = process.env.MANAGER_TEST_URL || 'http://127.0.0.1:17002';
const write = process.argv.includes('--write');
const tabs = await (await fetch(`http://127.0.0.1:${debug}/json`)).json();
const page = tabs.find(t=>t.type==='page'&&t.url.startsWith(base));
assert(page, 'Open the manager page in the dedicated debugging Chrome first');
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
let next=0;
const pending=new Map(),errors=[];
ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails.text);if(pending.has(m.id)){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(m.error):p.resolve(m.result);}};
function cdp(method,params={}){return new Promise((resolve,reject)=>{const id=++next;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});}
async function evaluate(expression){const r=await cdp('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw new Error(JSON.stringify(r.exceptionDetails));return r.result.value;}
async function waitFor(expression){for(let i=0;i<60;i++){if(await evaluate(expression))return;await new Promise(r=>setTimeout(r,100));}throw new Error('Timed out: '+expression);}
async function click(selector){await evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);}
async function fill(name,value){await evaluate(`{const input=document.querySelector('[name="${name}"]');input.value=${JSON.stringify(value)};input.dispatchEvent(new Event('input',{bubbles:true}));}`);}
async function go(name){await evaluate(`location.hash=${JSON.stringify(name)}`);await waitFor(`document.querySelector('#crumb')?.textContent===${JSON.stringify({overview:'主页',models:'模型管理',config:'配置文件',skills:'Skills',knowledge:'知识库',tools:'工具',chat:'简单对话',memories:'记忆管理',logs:'运行日志'}[name])}`);}
try{
 await cdp('Runtime.enable');
 await cdp('Emulation.setDeviceMetricsOverride',{width:1440,height:1080,deviceScaleFactor:1,mobile:false});
 await waitFor(`document.querySelector('#main .loading')===null`);
 assert.equal(await evaluate(`document.querySelector('#connection')`),null);
 for(const name of ['overview','models','skills','knowledge','tools','chat','memories','logs','config']){
  await go(name);
  assert(await evaluate(`document.documentElement.scrollWidth<=innerWidth`), name+' horizontal overflow');
  if(['models','skills','knowledge','tools'].includes(name)){
   await click(name==='skills'?'[data-action="skill-import"]':'[data-action="new"]');
   await waitFor(`document.querySelector('#editor').open`);
   await click('#cancel-dialog');
  }
 }
 await go('overview');
 await click('[data-action="quick-guide"]');
 await waitFor(`document.querySelector('#crumb')?.textContent==='使用指引'`);
 assert.equal(await evaluate(`document.querySelectorAll('.guide-step').length`),4);
 assert.equal(await evaluate(`document.querySelectorAll('.guide-feature').length`),8);
 assert(await evaluate(`document.querySelector('.guide-code code')?.textContent.includes('corki')`));
 assert(await evaluate(`document.querySelector('.header-back')?.getAttribute('href')==='#overview'`));
 assert(await evaluate(`document.documentElement.scrollWidth<=innerWidth`),'Guide horizontal overflow');
 await click('.guide-step [data-action="quick-setup"]');await waitFor(`document.querySelector('.quick-dialog')?.open`);await click('.quick-cancel');
 await click('.guide-step [data-action="quick-launch"]');await waitFor(`document.querySelector('.quick-dialog')?.open`);await click('.quick-cancel');
 if(process.env.GUIDE_SCREENSHOT){await cdp('Page.enable');const guideShot=await cdp('Page.captureScreenshot',{format:'png',captureBeyondViewport:true});fs.writeFileSync(process.env.GUIDE_SCREENSHOT,Buffer.from(guideShot.data,'base64'));}
 await click('.header-back');await waitFor(`document.querySelector('#crumb')?.textContent==='主页'`);
 await go('models');
 await evaluate(`document.querySelector('#search').value='nothing-should-match';document.querySelector('#search').dispatchEvent(new Event('input',{bubbles:true}))`);
 assert(await evaluate(`document.querySelector('.empty')!==null`));
 if(write){
  assert(base.endsWith(':17003'),'Write QA requires the disposable test server on 17003');
  const originalDefault=await evaluate(`JSON.stringify(state.default)`);
  await go('models');await click('[data-action="quick-add"]');
  await waitFor(`document.querySelector('.quick-dialog')?.open`);
  assert.equal(await evaluate(`document.querySelector('.quick-header h2').textContent`),'快速添加');
  assert.equal(await evaluate(`document.querySelector('.quick-dialog [data-models] input').type`),'checkbox');
  await click('.quick-dialog [data-models] summary');
  await evaluate(`Array.from(document.querySelectorAll('.quick-dialog [data-models] input')).slice(0,2).forEach(input=>input.click())`);
  assert.equal(await evaluate(`document.querySelector('.quick-dialog [data-models] summary span').textContent`),'已选 2 个模型');
  await evaluate(`document.querySelector('.quick-dialog [data-key]').value='browser-fixture-key'`);
  await click('.quick-confirm');await waitFor(`!document.querySelector('.quick-dialog')`);
  await waitFor(`state.providers.some(p=>p.id==='deepseek')`);
  assert.equal(await evaluate(`Object.keys(state.providers.find(p=>p.id==='deepseek').models).length`),2);
  assert.equal(await evaluate(`JSON.stringify(state.default)`),originalDefault);
  await go('models');
  await evaluate(`document.querySelector('#search').value='';document.querySelector('#search').dispatchEvent(new Event('input',{bubbles:true}))`);
  await click('[data-action="new"]');
  await fill('id','browser-test');await fill('base_url','https://example.com');await fill('api_key','fixture-key');await click('#save');
  await waitFor(`!document.querySelector('#editor').open`);
  assert(await evaluate(`document.body.innerText.includes('browser-test')`));
  await evaluate(`location.hash='models/browser-test'`);await waitFor(`selectedProvider==='browser-test'`);await click('[data-action="new"]');
  assert.equal(await evaluate(`document.querySelector('#editor select[name="provider"]')`),null);
  await click('#editor .provider-select-field .select-trigger');
  await click('#provider-select-options [data-value="deepseek"]');
  assert.equal(await evaluate(`document.querySelector('#editor [name="provider"]').value`),'deepseek');
  await click('#editor .provider-select-field .select-trigger');
  await click('#provider-select-options [data-value="browser-test"]');
  await fill('id','test-model');await fill('context_window','131072');await click('#save');
  await waitFor(`!document.querySelector('#editor').open`);
  await evaluate(`location.hash='models/browser-test/test-model'`);await waitFor(`document.querySelector('#main [name=context_window]')`);
  await fill('context_window','262144');await fill('reasoning_effort','low');
  await click('[data-action="model-save"]');
  await waitFor(`state.providers.find(p=>p.id==='browser-test').models['test-model'].context_window===262144`);
  assert(await evaluate(`state.providers.find(p=>p.id==='browser-test').models['test-model'].reasoning_effort==='low'`));
  await evaluate(`location.hash='models/browser-test'`);await waitFor(`selectedProvider==='browser-test' && !selectedModel`);
  await click('[data-action="default"][data-id="test-model"]');await click('#save');
  await waitFor(`!document.querySelector('#editor').open`);
  assert(await evaluate(`state.default.model==='test-model' && state.default.provider==='browser-test'`));
  await go('knowledge');await click('[data-action="new"]');await click('[data-method="text"]');await fill('name','browser-note');await fill('content','# 浏览器测试\n通过页面保存');await click('#save');
  await waitFor(`!document.querySelector('#editor').open`);
  assert(await evaluate(`document.body.innerText.includes('浏览器测试')`));
  await click('[data-action="file"]');await waitFor(`document.querySelector('#editor').open`);await fill('content','# 浏览器测试\n已编辑');await click('#save');await waitFor(`!document.querySelector('#editor').open`);
  await click('[data-action="delete"]');await click('#save');await waitFor(`!document.querySelector('#editor').open`);
  assert(await evaluate(`document.querySelector('.empty')!==null`));
 }
 await go('overview');
 await cdp('Page.enable');
 let shot=await cdp('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
 fs.writeFileSync('/tmp/corki-manager-desktop-qa.png',Buffer.from(shot.data,'base64'));
 await cdp('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
 for(const name of ['overview','models','skills','knowledge','tools','chat','memories','logs','config']){
  await go(name);assert(await evaluate(`document.documentElement.scrollWidth<=innerWidth`),'Mobile overflow: '+name);
 }
 await go('overview');await click('#menu');
 assert(await evaluate(`document.body.classList.contains('open-nav')`));await click('#scrim');
 await waitFor(`!document.body.classList.contains('open-nav')`);
 await evaluate(`Promise.all(document.getAnimations().map(animation=>animation.finished.catch(()=>{})))`);
 shot=await cdp('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
 fs.writeFileSync('/tmp/corki-manager-mobile-qa.png',Buffer.from(shot.data,'base64'));
 assert.equal(errors.length,0,errors.join('\n'));
 console.log('PASS: 9 desktop/mobile routes, dialogs, search, mobile navigation, no runtime errors'+(write?', provider/model/default/document CRUD':''));
}finally{await cdp('Emulation.clearDeviceMetricsOverride');ws.close();}
