// Disposable manager required on 17003, rooted at /tmp/corki-skills-qa.
import assert from 'node:assert/strict';
import fs from 'node:fs';
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
const shot=async name=>{await ev('Promise.race([Promise.all(document.getAnimations().map(a=>a.finished.catch(()=>{}))),new Promise(r=>setTimeout(r,500))])');const r=await cdp('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync('/tmp/'+name+'.png',Buffer.from(r.data,'base64'))};
try{
 await cdp('Runtime.enable');await cdp('Page.enable');await cdp('Page.bringToFront');
 assert(!await ev('window.CorkiConfig?.dirty || window.CorkiSkills?.dirty()'),'Unsaved draft');
 await cdp('Page.navigate',{url:'http://127.0.0.1:17003/#models'});
 await wait('window.CorkiConfig && typeof state!=="undefined" && state?.providers?.some(p=>p.id==="fixture")');
 await cdp('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
 const open=async()=>{await click('[data-action="edit"][data-id="fixture"]');await wait('document.querySelector("#provider-key")');};
 const stored=async()=>await(await fetch('http://127.0.0.1:17003/api/provider-key?id=fixture')).json();
 await open();assert.equal(await ev('document.querySelector("#provider-key").value'),'********');
 await shot('corki-connection-controls');
 await click('#save');await wait('!document.querySelector("#editor").open');assert.equal((await stored()).api_key,'fixture-only');
 await open();await click('.secret-toggle');await wait('document.querySelector("#provider-key").type==="text"');assert.equal(await ev('document.querySelector("#provider-key").value'),'fixture-only');
 await click('.secret-toggle');assert.equal(await ev('document.querySelector("#provider-key").type'),'password');
 await click('#save');await wait('!document.querySelector("#editor").open');assert.equal((await stored()).api_key,'fixture-only');
 await open();await ev('document.querySelector("#provider-key").focus()');await cdp('Input.insertText',{text:'replacement-only'});
 assert.equal(await ev('document.querySelector("#provider-key").value'),'replacement-only');
 await click('#save');await wait('!document.querySelector("#editor").open');assert.equal((await stored()).api_key,'replacement-only');
 await open();await click('.secret-toggle');await wait('document.querySelector("#provider-key").type==="text"');assert.equal(await ev('document.querySelector("#provider-key").value'),'replacement-only');
 await click('#cancel-dialog');assert.deepEqual(errors,[]);
 console.log('PASS: masked initial value, reveal/hide, unchanged save, replacement save, persisted reveal; desktop screenshot.');
}finally{await cdp('Emulation.clearDeviceMetricsOverride');await cdp('Page.navigate',{url:original});ws.close();}
