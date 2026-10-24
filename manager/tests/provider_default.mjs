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
 await cdp('Runtime.enable');await cdp('Page.enable');
 assert(!await ev('window.CorkiConfig?.dirty || window.CorkiSkills?.dirty()'));
 await cdp('Page.navigate',{url:'http://127.0.0.1:17003/#models'});
 await wait('typeof state!=="undefined" && state?.providers?.some(p=>p.id==="target")');
 assert(!await ev(`document.querySelector('[data-action="provider-default"][data-id="current"]')`));
 assert(await ev(`document.querySelector('[data-action="provider-default"][data-id="empty"]').disabled`));
 assert(await ev(`document.querySelector('[data-action="provider-default"][data-id="target"]').nextElementSibling.dataset.action==="edit"`));
 await click('[data-action="edit"][data-id="target"]');assert(!await ev('document.querySelector("#editor").textContent.includes("设为默认")'));await click('#cancel-dialog');
 await click('[data-action="provider-default"][data-id="target"]');await wait('state.default.provider==="target"');
 const snapshot=await(await fetch('http://127.0.0.1:17003/api/snapshot')).json();assert.deepEqual(snapshot.default,{provider:'target',model:'demo'});
 assert(!await ev(`document.querySelector('[data-action="provider-default"][data-id="target"]')`));
 assert.deepEqual(errors,[]);console.log('PASS: card button placement, empty model guard, no dialog button, persisted default.');
}finally{await cdp('Page.navigate',{url:original});ws.close();}
