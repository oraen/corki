// Destructive browser tests are restricted to a disposable server on port 17003.
import fs from 'node:fs';
import assert from 'node:assert/strict';
const tabs=await(await fetch('http://127.0.0.1:9225/json')).json();
const tab=tabs.find(t=>t.type==='page'&&t.url.startsWith('http://127.0.0.1:17003/'));
assert(tab,'Open disposable editor QA server on port 17003');
const ws=new WebSocket(tab.webSocketDebuggerUrl);await new Promise(r=>ws.onopen=r);
let id=0;const pending=new Map(),errors=[];
ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails.text);if(pending.has(m.id)){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(m.error):p.resolve(m.result)}};
const cdp=(method,params={})=>new Promise((resolve,reject)=>{const n=++id;pending.set(n,{resolve,reject});ws.send(JSON.stringify({id:n,method,params}))});
const ev=async expression=>{const r=await cdp('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value};
async function wait(expr){for(let n=0;n<100;n++){if(await ev(`Boolean(${expr})`))return;await new Promise(r=>setTimeout(r,100));}throw Error('Timeout '+expr)}
const click=selector=>ev(`document.querySelector(${JSON.stringify(selector)}).click()`);
const action=name=>click(`[data-config-action="${name}"]`);
const replace=text=>ev(`CorkiConfig.editor.dispatch({changes:{from:0,to:CorkiConfig.editor.state.doc.length,insert:${JSON.stringify(text)}}})`);
const shot=async name=>{await ev(`Promise.all(document.getAnimations().map(a=>a.finished.catch(()=>{})))`);const r=await cdp('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync('/tmp/'+name+'.png',Buffer.from(r.data,'base64'))};
const fixtureApi=async (path,body)=>{
 const response=await fetch('http://127.0.0.1:17003/api/'+path,body?{method:'POST',headers:{'Content-Type':'application/json','X-Corki-Manager':'1'},body:JSON.stringify(body)}:{});
 const data=await response.json();assert(response.ok,data.error);return data;
};
const before=await fixtureApi('config');
assert(before.path.startsWith('/tmp/corki-manager-editor-qa/')||before.path.startsWith('/private/tmp/corki-manager-editor-qa/'),'Dedicated QA home required');
const baseline={default:{provider:'openai',model:'demo-model'},providers:{openai:{api_mode:'responses',base_url:'http://127.0.0.1:17001',api_key:'fixture-only',models:{'demo-model':{context_window:131072,reasoning_effort:'medium'}}}},editor_test:Object.fromEntries(Array.from({length:40},(_,n)=>['entry_'+n,'value '+n])),test_run:Date.now()};
await fixtureApi('mutate',{kind:'config',action:'save',revision:before.revision,content:'// Configuration test fixture\n'+JSON.stringify(baseline,null,2)+'\n'});
baseline.providers.openai.models['demo-model'].reasoning_effort='low';
await fixtureApi('mutate',{kind:'config',action:'save',revision:(await fixtureApi('config')).revision,content:'// Configuration test fixture\n'+JSON.stringify(baseline,null,2)+'\n'});
try{
 await cdp('Runtime.enable');await cdp('Page.enable');await cdp('Page.navigate',{url:'http://127.0.0.1:17003/#config'});await cdp('Page.reload');await new Promise(r=>setTimeout(r,400));await cdp('Emulation.setDeviceMetricsOverride',{width:1500,height:1100,deviceScaleFactor:1,mobile:false});
 await wait(`window.CorkiConfig?.doc && CorkiConfig.editor`);
 const original=await ev('CorkiConfig.doc.content');
 const initialCount=await ev('CorkiConfig.doc.versions.length');
 assert.equal(await ev(`document.querySelector('#json-editor .cm-content').getAttribute('contenteditable')`),'false');
 assert(await ev(`document.querySelectorAll('#json-editor .cm-line span').length>5`),'JSON highlighting absent');
 await action('edit');assert.equal(await ev(`document.querySelector('#json-editor .cm-content').getAttribute('contenteditable')`),'true');
 await replace(original+'\n');await wait('CorkiConfig.dirty');
 await replace(original);await wait('!CorkiConfig.dirty');
 await replace('{"bad":');await action('validate');await wait(`document.querySelector('#config-status').dataset.kind==='error'`);
 await action('save');await wait(`!CorkiConfig.busy`);
 assert.equal(await ev('CorkiConfig.doc.versions.length'),initialCount);
 await wait(`document.querySelector('.cm-lintRange-error, .cm-lintPoint-error')!==null`);
 const edited=original.replace('"reasoning_effort": "low"','"reasoning_effort": "high"')+'\n';
 await replace(edited);await action('validate');await wait(`document.querySelector('#config-status').dataset.kind==='success'`);
 await ev(`location.hash='models'`);await wait(`route==='models' && !document.querySelector('#config-workspace')`);
 await ev(`location.hash='config'`);await wait(`route==='config' && CorkiConfig.editor && document.querySelector('#config-workspace')`);
 assert.equal(await ev('CorkiConfig.draft'),edited,'Draft lost on navigation');
 await action('format');assert((await ev('CorkiConfig.draft')).startsWith('// Configuration test fixture'));
 await action('save');await wait(`!CorkiConfig.busy && !CorkiConfig.editing`);
 assert.equal(await ev('CorkiConfig.doc.versions.length'),Math.min(initialCount+1,20));
 assert((await ev('CorkiConfig.doc.content')).includes('"high"'));
 await shot('corki-config-editor-desktop');
 await action('search');assert(await ev(`document.querySelector('.cm-search')!==null`));
 await ev(`document.querySelector('.cm-search [name=close]').click()`);
 // History selection replaces the main editor content, while comparison stays historical/current.
 const olderId=await ev('CorkiConfig.doc.versions[1].id');
 await click(`[data-config-action="select-version"][data-id="${olderId}"]`);
 await wait(`CorkiConfig.viewingId===${JSON.stringify(olderId)} && CorkiConfig.editor`);
 assert.equal(await ev(`document.querySelector('#json-editor .cm-content').getAttribute('contenteditable')`),'false');
 assert.equal(await ev(`document.querySelectorAll('.version-item.selected').length`),1);
 assert.equal(await ev(`document.querySelector('.version-item.selected [data-config-action="select-version"]').dataset.id`),olderId);
 assert(await ev(`api('config?id='+${JSON.stringify(olderId)}).then(v=>v.content===CorkiConfig.editor.state.doc.toString())`));
 assert(await ev(`document.querySelector('#config-edit').disabled`));
 assert.equal(await ev(`document.querySelectorAll('[data-config-action="compare-selected"],[data-config-action="view-version"],#version-preview').length`),0);
 await action('current');await wait(`!CorkiConfig.viewingId && CorkiConfig.editor.state.doc.toString()===CorkiConfig.doc.content`);
 await action('compare');await wait(`CorkiConfig.merge && CorkiConfig.merge.chunks.length>0`);
 assert.equal(await ev(`document.querySelectorAll('#diff-left,#diff-right,[data-config-action="swap"]').length`),0);
 await action('next');assert(await ev(`document.querySelector('.diff-position').textContent.startsWith('1 /')`));
 await action('previous');
 assert(await ev(`document.querySelector('.cm-collapsedLines')!==null`));
 await click('#diff-collapse');await wait(`CorkiConfig.merge && !document.querySelector('.cm-collapsedLines')`);
 // Historical vs current, responsive diff.
 await action('current');await click('[data-config-action="compare-current"][data-id="'+await ev('CorkiConfig.doc.versions[1].id')+'"]');
 await wait(`CorkiConfig.merge && CorkiConfig.merge.chunks.length`);
 assert.equal(await ev('document.querySelector(".config-view-back").textContent.trim()'),'返回配置');
 await action('back-to-current');await wait(`CorkiConfig.tab==='current' && CorkiConfig.editor`);
 await action('compare');await wait(`CorkiConfig.merge && CorkiConfig.merge.chunks.length`);
 await shot('corki-config-diff-desktop');
 await cdp('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
 assert(await ev('document.documentElement.scrollWidth<=innerWidth'),'Diff mobile page overflow');
 await action('unified');await wait(`CorkiConfig.unified && CorkiConfig.chunks().length>0`);
 assert(await ev(`document.querySelector('.cm-deletedChunk')!==null`));
 await action('next');assert(await ev(`document.querySelector('.diff-position').textContent.startsWith('1 /')`));
 assert(await ev('document.documentElement.scrollWidth<=innerWidth'),'Unified mobile page overflow');
 await shot('corki-config-diff-mobile');
 await action('current');await wait(`CorkiConfig.editor`);
 assert(await ev('document.documentElement.scrollWidth<=innerWidth'),'Editor mobile page overflow');
 await shot('corki-config-editor-mobile');
 // Cancel confirmation keeps or discards the buffer as selected.
 await action('edit');await replace(original+'\n\n');await action('cancel');await wait(`document.querySelector('#config-confirm').open`);
 await action('dismiss');assert(await ev('CorkiConfig.dirty'));
 await action('cancel');await action('confirm');await wait(`!CorkiConfig.editing`);assert(!(await ev('CorkiConfig.dirty')));
 // A concurrent write must not be overwritten by a stale browser draft.
 await action('edit');const current=await ev('CorkiConfig.doc.content');await replace(current+'\n');
 await ev(`api('mutate',{kind:'config',action:'save',content:CorkiConfig.doc.content+'\\n\\n',revision:CorkiConfig.doc.revision})`);
 await action('save');await wait(`!CorkiConfig.busy && document.querySelector('#config-status').textContent.includes('冲突')`);
 assert(await ev('CorkiConfig.dirty'),'Conflict must preserve draft');
 await action('refresh');await action('confirm');await wait(`!CorkiConfig.editing && !CorkiConfig.dirty`);
 // Rollback remains available in the new history panel.
 await click('[data-config-action="restore"]');await action('confirm');await wait(`!CorkiConfig.busy && CorkiConfig.doc.versions[0].reason==='回滚历史版本'`);
 assert.equal(errors.length,0,errors.join('\n'));
 console.log('PASS: highlighting, edits, validation, inline read-only history, selection, current comparison, navigation, mobile, conflict, rollback');
}finally{await cdp('Emulation.clearDeviceMetricsOverride');ws.close()}
