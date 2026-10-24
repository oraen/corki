// Browser regression for the inline version history editor.
import fs from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import assert from 'node:assert/strict';
const base=process.env.MANAGER_TEST_URL||'http://127.0.0.1:17002';
const tabs=await(await fetch('http://127.0.0.1:9225/json')).json();
const tab=tabs.find(t=>t.type==='page'&&t.url.startsWith(base));
assert(tab,'Open manager in debugging Chrome');
const ws=new WebSocket(tab.webSocketDebuggerUrl);await new Promise(r=>ws.onopen=r);
let id=0;const pending=new Map(),errors=[];
ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails.text);if(pending.has(m.id)){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(m.error):p.resolve(m.result)}};
const cdp=(method,params={})=>new Promise((resolve,reject)=>{const n=++id;pending.set(n,{resolve,reject});ws.send(JSON.stringify({id:n,method,params}))});
const ev=async expression=>{const r=await cdp('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value};
async function wait(expr){for(let n=0;n<100;n++){if(await ev(`Boolean(${expr})`))return;await new Promise(r=>setTimeout(r,100));}throw Error('Timeout '+expr)}
const click=selector=>ev(`document.querySelector(${JSON.stringify(selector)}).click()`);
const action=name=>click(`[data-config-action="${name}"]`);
const replace=text=>ev(`CorkiConfig.editor.dispatch({changes:{from:0,to:CorkiConfig.editor.state.doc.length,insert:${JSON.stringify(text)}}})`);
const shot=async name=>{const r=await cdp('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(join(tmpdir(),name+'.png'),Buffer.from(r.data,'base64'))};

try{
 await cdp('Runtime.enable');await cdp('Page.enable');
 assert(!(await ev('window.CorkiConfig?.dirty')),'Unsaved user draft; skip reloading');
 await cdp('Page.navigate',{url:base+'/#config'});await cdp('Page.reload');
 await new Promise(r=>setTimeout(r,400));
 await ev(`location.hash='config'`);
 await wait('window.CorkiConfig?.doc && CorkiConfig.editor');
 const revision=await ev('CorkiConfig.doc.revision');
 const first=await ev('CorkiConfig.doc.versions[0].id');
 assert(await ev(`document.querySelector('.version-item strong').textContent.endsWith('（当前）')`));
 assert.equal(await ev(`document.querySelectorAll('[data-config-action="select-version"]').length`),await ev('CorkiConfig.doc.versions.length'));
 assert.equal(await ev(`document.querySelectorAll('[data-config-action="view-version"],[data-config-action="compare-selected"],#version-preview').length`),0);
 const older=await ev('CorkiConfig.doc.versions[1]?.id');
 if(older){
  await click(`[data-config-action="select-version"][data-id="${older}"]`);
  await wait(`CorkiConfig.viewingId===${JSON.stringify(older)} && CorkiConfig.editor`);
  assert(await ev(`api('config?id='+${JSON.stringify(older)}).then(v=>v.content===CorkiConfig.editor.state.doc.toString())`));
  assert.equal(await ev(`document.querySelector('#json-editor .cm-content').getAttribute('contenteditable')`),'false');
  assert.equal(await ev(`document.querySelectorAll('.version-item.selected').length`),1);
  assert(await ev(`document.querySelector('#config-edit').disabled`));
  await shot('corki-config-history-inline');
  await action('current');await wait('CorkiConfig.editor && !CorkiConfig.viewingId');
  await action('edit');await replace(await ev('CorkiConfig.doc.content')+'\n');
  await wait('CorkiConfig.dirty');
  await click(`[data-config-action="select-version"][data-id="${older}"]`);
  await wait(`document.querySelector('#config-confirm').open`);
  await action('dismiss');assert(await ev('CorkiConfig.dirty && !CorkiConfig.viewingId'));
  await click(`[data-config-action="select-version"][data-id="${older}"]`);
  await action('confirm');await wait(`CorkiConfig.viewingId===${JSON.stringify(older)} && !CorkiConfig.dirty`);
  await action('compare');await wait('CorkiConfig.merge || CorkiConfig.unified');
  assert.equal(await ev('CorkiConfig.left'),older);
  assert.equal(await ev(`document.querySelectorAll('#diff-left,#diff-right,[data-config-action="swap"]').length`),0);
  await action('current');await wait('CorkiConfig.editor');
 }
 await click(`[data-config-action="select-version"][data-id="${first}"]`);
 await wait('CorkiConfig.editor && !CorkiConfig.viewingId');
 assert(await ev(`CorkiConfig.editor.state.doc.toString()===CorkiConfig.doc.content`));
 assert(await ev(`document.querySelector('.version-item.selected [data-config-action="select-version"]').dataset.id===${JSON.stringify(first)}`));
 await cdp('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
 assert(await ev('document.documentElement.scrollWidth<=innerWidth'));
 assert(await ev(`api('config').then(doc=>doc.revision===${JSON.stringify(revision)})`),'History navigation changed configuration');
 assert.equal(errors.length,0,errors.join('\n'));
 console.log('PASS: inline historical content, selected highlight, read-only editor, draft confirmation, fixed current comparison, mobile layout; configuration unchanged');
}finally{await cdp('Emulation.clearDeviceMetricsOverride');ws.close()}
