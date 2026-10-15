// Run against a disposable manager home containing one user and one built-in document.
import assert from 'node:assert/strict';
import fs from 'node:fs';

const base=process.env.MANAGER_TEST_URL||'http://127.0.0.1:17006';
const port=process.env.CHROME_DEBUG_PORT||'9225';
const target=await(await fetch(`http://127.0.0.1:${port}/json/new?about:blank`,{method:'PUT'})).json();
const socket=new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve,reject)=>{socket.onopen=resolve;socket.onerror=reject;});
let serial=0;const pending=new Map();
socket.onmessage=event=>{const message=JSON.parse(event.data),item=pending.get(message.id);if(!item)return;pending.delete(message.id);message.error?item.reject(new Error(message.error.message)):item.resolve(message.result);};
function cdp(method,params={}){return new Promise((resolve,reject)=>{const id=++serial;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));});}
async function evaluate(expression){const result=await cdp('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(result.exceptionDetails)throw new Error(result.exceptionDetails.text);return result.result.value;}
async function wait(expression){for(let i=0;i<70;i++){if(await evaluate(expression))return;await new Promise(resolve=>setTimeout(resolve,100));}throw new Error(`Timed out: ${expression}`);}
try{
 await cdp('Page.enable');await cdp('Runtime.enable');
 await cdp('Emulation.setDeviceMetricsOverride',{width:1440,height:900,deviceScaleFactor:1,mobile:false});
 await cdp('Page.navigate',{url:base+'/#knowledge'});
 await wait(`document.querySelector('[data-knowledge-group="user"] .knowledge-card') && document.querySelector('[data-knowledge-group="builtin"] .knowledge-card')`);
 assert(await evaluate(`document.querySelector('[data-knowledge-group="user"]').open`));
 assert.equal(await evaluate(`document.querySelector('[data-knowledge-group="builtin"]').open`),false);
 assert.equal(await evaluate(`document.querySelectorAll('[data-knowledge-group="user"] .knowledge-card').length`),1);
 assert.equal(await evaluate(`document.querySelectorAll('[data-knowledge-group="builtin"] .knowledge-card').length`),1);
 assert.equal(await evaluate(`document.querySelectorAll('[data-knowledge-group="builtin"] [data-action="delete"]').length`),0);
 await evaluate(`document.querySelector('[data-knowledge-group="user"] .toggle').click()`);
 await wait(`document.querySelector('[data-knowledge-group="user"] .toggle')?.getAttribute('aria-checked')==='false'`);
 const disabled=await(await fetch(base+'/api/snapshot')).json();
 assert.equal(disabled.files.find(f=>f.kind==='knowledge'&&f.group==='user').enabled,false);
 await cdp('Page.reload',{ignoreCache:true});
 await wait(`document.querySelector('[data-knowledge-group="user"] .toggle')?.getAttribute('aria-checked')==='false'`);
 await evaluate(`document.querySelector('[data-knowledge-group="user"] .toggle').click()`);
 await wait(`document.querySelector('[data-knowledge-group="user"] .toggle')?.getAttribute('aria-checked')==='true'`);
 await evaluate(`document.querySelector('[data-knowledge-group="builtin"] summary').click()`);
 await wait(`document.querySelector('[data-knowledge-group="builtin"]').open`);
 await evaluate(`document.querySelector('[data-knowledge-group="builtin"] [data-action="file"]').click()`);
 await wait(`document.querySelector('#editor').open`);
 assert(await evaluate(`document.querySelector('#editor textarea[name="content"]').readOnly`));
 await evaluate(`document.querySelector('#close-dialog').click()`);
 await evaluate(`document.querySelector('[data-knowledge-group="user"] [data-action="file"]').click()`);
 await wait(`document.querySelector('#editor').open`);
 assert.equal(await evaluate(`document.querySelector('#editor textarea[name="content"]').readOnly`),false);
 await evaluate(`document.querySelector('#close-dialog').click()`);
 if(process.env.KNOWLEDGE_SCREENSHOT){const shot=await cdp('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(process.env.KNOWLEDGE_SCREENSHOT,Buffer.from(shot.data,'base64'));}
 console.log('PASS: knowledge groups, collapse, persistent toggle, built-in read-only, user edit');
}finally{socket.close();await fetch(`http://127.0.0.1:${port}/json/close/${target.id}`);}
