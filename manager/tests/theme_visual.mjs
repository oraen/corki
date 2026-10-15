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
const upload=async(name,body)=>ev(`(()=>{const dt=new DataTransfer();dt.items.add(new File([${JSON.stringify(body)}],${JSON.stringify(name)},{type:'text/markdown'}));const el=document.querySelector('#skill-file-input');el.files=dt.files;el.dispatchEvent(new Event('change',{bubbles:true}));})()`);
try{
 await cdp('Runtime.enable');await cdp('Page.enable');await cdp('Page.bringToFront');
 assert(!await ev('window.CorkiConfig?.dirty'),'Unsaved draft');
 await cdp('Page.navigate',{url:'http://127.0.0.1:17003/#overview'});
 await wait('typeof state!=="undefined" && state?.providers?.length===2');
 assert(await ev(`state.files.every(f=>f.path.includes('/corki-skills-qa/'))`));
 await cdp('Emulation.setDeviceMetricsOverride',{width:1440,height:1080,deviceScaleFactor:1,mobile:false});
 const go=async route=>{await ev(`location.hash=${JSON.stringify(route)}`);await wait(`route===${JSON.stringify(route.split('/')[0])}`);await new Promise(r=>setTimeout(r,150));};
 for(const name of ['overview','models','skills','knowledge','tools','memories','logs','config']){
  await go(name);if(name==='config')await wait('CorkiConfig.editor');
  assert(await ev('document.documentElement.scrollWidth<=innerWidth'),name+' overflow');
  await shot('corki-orange-'+name);
  if(['models','skills','knowledge','tools'].includes(name)){
   await click(name==='skills'?'[data-action="skill-import"]':'[data-action="new"]');
   await shot('corki-orange-'+name+'-dialog');await click('#cancel-dialog');
  }
 }
 await click('.version-item:nth-child(2) [data-config-action="select-version"]');await wait('CorkiConfig.viewingId && CorkiConfig.editor');await shot('corki-orange-config-history');await click('[data-config-action="current"]');
 await click('[data-config-action="compare-current"]');await wait('CorkiConfig.merge || CorkiConfig.unified');await shot('corki-orange-config-diff');
 await go('models/openai');await shot('corki-orange-model-list');await go('models/openai/demo-model');await shot('corki-orange-model-detail');
 await go('skills');await click('[data-skill-group="corki"] [data-action="skill-view"]');await wait('CorkiSkills.editor');await click('[data-sw="edit"]');await shot('corki-orange-skill-editor');await click('[data-sw="close"]');
 await cdp('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
 for(const name of ['overview','models','skills','knowledge','tools','memories','logs','config']){
  await go(name);if(name==='config')await wait('CorkiConfig.editor || CorkiConfig.merge || CorkiConfig.unified');
  assert(await ev('document.documentElement.scrollWidth<=innerWidth'),name+' mobile overflow');
  if(['overview','skills','config'].includes(name))await shot('corki-orange-'+name+'-mobile');
 }
 await go('skills');await click('[data-action="skill-import"]');await shot('corki-orange-import-mobile');await click('#cancel-dialog');
 assert(await ev('document.querySelector("img.brand-mark")?.naturalWidth>0'));
 assert.deepEqual(errors,[]);console.log('PASS: orange brand, 8 desktop/mobile pages, model levels, 4 form dialogs, history/diff, skill editor.');
}finally{await cdp('Emulation.clearDeviceMetricsOverride');await cdp('Page.navigate',{url:original});ws.close();}
