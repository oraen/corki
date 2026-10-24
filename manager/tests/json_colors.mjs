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
const upload=async(name,body)=>ev(`(()=>{const dt=new DataTransfer();dt.items.add(new File([${JSON.stringify(body)}],${JSON.stringify(name)},{type:'text/markdown'}));const el=document.querySelector('#skill-file-input');el.files=dt.files;el.dispatchEvent(new Event('change',{bubbles:true}));})()`);
const expected={'"label"':'rgb(37, 99, 166)','"Corki"':'rgb(36, 118, 79)','512000':'rgb(133, 84, 179)','true':'rgb(196, 71, 50)','false':'rgb(196, 71, 50)','null':'rgb(125, 101, 145)','// JSONC syntax colors':'rgb(122, 132, 147)','"true"':'rgb(36, 118, 79)'};
async function checkColors(root,values=expected){
 await wait(`document.querySelectorAll(${JSON.stringify(root+' .cm-line span')}).length>8`);
 const spans=await ev(`[...document.querySelectorAll(${JSON.stringify(root+' .cm-line span')})].map(e=>[e.textContent,getComputedStyle(e).color])`);
 for(const [token,color] of Object.entries(values))assert(spans.some(([text,actual])=>text===token&&actual===color),root+' '+token+' '+JSON.stringify(spans));
}
try{
 await cdp('Runtime.enable');await cdp('Page.enable');await cdp('Page.bringToFront');
 assert(!await ev('window.CorkiConfig?.dirty || window.CorkiSkills?.dirty()'),'Unsaved draft');
 await cdp('Page.navigate',{url:'http://127.0.0.1:17003/#config'});
 await wait('window.CorkiConfig?.editor');
 assert(await ev('CorkiConfig.doc.path.includes("/corki-skills-qa/")'));
 await cdp('Emulation.setDeviceMetricsOverride',{width:1440,height:1080,deviceScaleFactor:1,mobile:false});
 await checkColors('#json-editor');await shot('corki-json-colors');
 await click('.version-item:nth-child(2) [data-config-action="select-version"]');await wait('CorkiConfig.viewingId && CorkiConfig.editor');await checkColors('#json-editor');await click('[data-config-action="current"]');
 await click('[data-config-action="compare-current"]');await wait('CorkiConfig.merge || CorkiConfig.unified');
 await checkColors('#json-diff',{'512':expected['512000'],'256':expected['512000'],'000':expected['512000']});await shot('corki-json-colors-diff');
 await ev('location.hash="skills"');await wait('route==="skills"');await click('[data-skill-group="corki"] [data-action="skill-view"]');await wait('CorkiSkills.editor');
 await click('[data-sw-file="syntax.jsonc"]');await wait('CorkiSkills.editor && CorkiSkills.current==="syntax.jsonc"');await checkColors('#sw-content');await shot('corki-json-colors-skill');
 await click('[data-sw="close"]');
 assert.deepEqual(errors,[]);console.log('PASS: distinct key/string/number/boolean/null/comment colors; quoted true stays a string; config, history, diff, and Skills JSONC agree.');
}finally{await cdp('Emulation.clearDeviceMetricsOverride');await cdp('Page.navigate',{url:original});ws.close();}
