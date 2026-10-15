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
 await cdp('Runtime.enable');await cdp('Page.enable');
 assert(!await ev('window.CorkiConfig?.dirty || window.CorkiSkills?.dirty()'));
 const file='/tmp/corki-skills-qa/home/settings.json';
 fs.writeFileSync(file,JSON.stringify({refresh_test:'initial'}));
 await cdp('Page.navigate',{url:'http://127.0.0.1:17003/#config'});
 await wait('window.CorkiConfig?.editor && !CorkiConfig.loading');
 assert(await ev('CorkiConfig.doc.path.includes("/corki-skills-qa/")'));
 fs.writeFileSync(file,JSON.stringify({refresh_test:'same-page'}));
 await click('#nav a[href="#config"]');await wait('CorkiConfig.draft.includes("same-page") && !CorkiConfig.loading');
 await click('#nav a[href="#models"]');await wait('route==="models"');
 fs.writeFileSync(file,JSON.stringify({refresh_test:'reentry'}));
 await click('#nav a[href="#config"]');await wait('CorkiConfig.draft.includes("reentry") && !CorkiConfig.loading');
 await click('#config-edit');await ev('CorkiConfig.editor.dispatch({changes:{from:0,to:CorkiConfig.editor.state.doc.length,insert:JSON.stringify({refresh_test:"draft"})}})');
 fs.writeFileSync(file,JSON.stringify({refresh_test:'latest'}));
 await click('#nav a[href="#config"]');await wait('document.querySelector("#config-confirm").open');
 await click('#config-confirm [data-config-action="dismiss"]');assert(await ev('CorkiConfig.draft.includes("draft")'));
 await click('#nav a[href="#config"]');await click('#config-confirm [data-config-action="confirm"]');await wait('CorkiConfig.draft.includes("latest") && !CorkiConfig.loading');
 assert.deepEqual(errors,[]);console.log('PASS: repeat click fetches disk, reentry fetches disk, draft cancel preserves changes, confirm loads latest.');
}finally{await cdp('Page.navigate',{url:original});ws.close();}
