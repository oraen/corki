// Disposable manager required on 17003, rooted at /tmp/corki-skills-qa.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
const tabs=await(await fetch('http://127.0.0.1:9225/json')).json();
const tab=tabs.find(t=>t.type==='page'&&t.url.startsWith('http://127.0.0.1:1700'));
assert(tab);const original=tab.url;
const ws=new WebSocket(tab.webSocketDebuggerUrl);await new Promise(r=>ws.onopen=r);
let seq=0;const pending=new Map(),errors=[];
ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails.text);if(pending.has(m.id)){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(m.error):p.resolve(m.result)}};
const cdp=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}))});
const ev=async expression=>{const r=await cdp('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value};
async function wait(expr){for(let n=0;n<100;n++){if(await ev(`Boolean(${expr})`))return;await new Promise(r=>setTimeout(r,100));}throw Error('Timeout '+expr)}
const click=s=>ev(`document.querySelector(${JSON.stringify(s)}).click()`);
const shot=async name=>{await ev('Promise.race([Promise.all(document.getAnimations().map(a=>a.finished.catch(()=>{}))),new Promise(r=>setTimeout(r,500))])');const r=await cdp('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(join(tmpdir(),name+'.png'),Buffer.from(r.data,'base64'))};
try{
 await cdp('Runtime.enable');await cdp('Page.enable');await cdp('Page.bringToFront');
 assert(!await ev('window.CorkiConfig?.dirty || window.CorkiSkills?.dirty()'),'Unsaved draft');
 await cdp('Page.navigate',{url:'http://127.0.0.1:17003/#models'});
 await wait('typeof state!=="undefined" && state?.providers?.some(p=>p.id==="fixture")');
 await cdp('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
 await click('[data-action="edit"][data-id="fixture"]');
 assert.equal(await ev('document.querySelector("#fields [name=id]").disabled'),false);
 assert.equal(await ev('!!document.querySelector("select[name=api_mode]")'),false);
 await click('.select-trigger');await shot('corki-protocol-menu');
 await click('[role=option][data-value=chat_completions]');
 assert.equal(await ev('document.querySelector("[name=api_mode]").value'),'chat_completions');
 await click('.select-trigger');await cdp('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape'});
 assert(await ev('document.querySelector("#editor").open && document.querySelector("#protocol-options").hidden'));
 await cdp('Input.dispatchKeyEvent',{type:'keyDown',key:'ArrowDown',code:'ArrowDown'});
 await cdp('Input.dispatchKeyEvent',{type:'keyDown',key:'Home',code:'Home'});
 await cdp('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter'});
 await cdp('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter'});
 assert.equal(await ev('document.querySelector("[name=api_mode]").value'),'responses');
 await ev('document.querySelector("#fields [name=id]").value="renamed"');
 await click('#save');await wait('!document.querySelector("#editor").open');
 const snapshot=await(await fetch('http://127.0.0.1:17003/api/snapshot')).json();
 assert.equal(snapshot.default.provider,'renamed');assert(snapshot.providers.some(p=>p.id==='renamed'&&p.models.demo&&p.has_key));assert(!snapshot.providers.some(p=>p.id==='fixture'));
 await click('[data-action="edit"][data-id="renamed"]');await click('.secret-toggle');await wait('document.querySelector("#provider-key").type==="text"');assert.equal(await ev('document.querySelector("#provider-key").value'),'fixture-only');
 await click('#cancel-dialog');assert.deepEqual(errors,[]);
 console.log('PASS: editable provider name, custom protocol menu, selection, keyboard, Escape, rename updates default and preserves models/key.');
}finally{await cdp('Emulation.clearDeviceMetricsOverride');await cdp('Page.navigate',{url:original});ws.close();}
