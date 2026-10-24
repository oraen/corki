// Requires agent-browser and an isolated fake-login manager fixture.
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import os from 'node:os';
import path from 'node:path';
import {readFile,writeFile} from 'node:fs/promises';
const base=process.env.MANAGER_TEST_URL;
assert(base,'MANAGER_TEST_URL is required');
const initial=await (await fetch(base+'/api/config')).json();
assert(initial.path.includes('corki-adaptor-ui-qa-'),'Disposable home required');
const exec=promisify(execFile),session='corki-adaptor-ui-'+process.pid;
const browser=(...args)=>exec('agent-browser',['--session',session,...args],{maxBuffer:1024*1024});
const state=async()=>await (await fetch(base+'/api/adaptor')).json();
async function wait(expression){await browser('eval',`(async()=>{for(let i=0;i<1200;i++){if(${expression})return true;await new Promise(r=>setTimeout(r,50))}throw Error('UI condition timed out')})()`)}
const oldPort=(await state()).port;

try{
 await browser('open',base);
 await wait(`document.querySelector('[data-action="open-adaptor"]')`);
 await browser('click','[data-action="open-adaptor"]');
 await wait(`document.querySelector('[data-provider="openai"]')`);
 await browser('snapshot','-i');
 await wait(`document.querySelector('[data-provider="openai"]').disabled && document.querySelector('.adaptor-provider-switch.blocked').dataset.tooltip==='需要先开启适配网关'`);
 await browser('eval',`(()=>{const original=window.fetch;window.fetch=async(...args)=>{if(String(args[0]).includes('/api/mutate')){window.fetch=original;await new Promise(resolve=>window.releaseAdaptorRequest=resolve);}return original(...args)};window.gatewayHeight=document.querySelector('.adaptor-gateway').getBoundingClientRect().height;})()`);
 await browser('click','[data-power]');
 await wait(`document.querySelector('.adaptor-progress[open]') && window.releaseAdaptorRequest`);
 await browser('screenshot',path.join(os.tmpdir(),'corki-adaptor-progress.png'));
 await browser('eval',`(()=>{if(document.querySelector('.adaptor-gateway').getBoundingClientRect().height!==window.gatewayHeight)throw Error('Progress changed page layout');window.releaseAdaptorRequest();})()`);
 await wait(`document.querySelector('[data-power]').textContent==='关闭网关' && !document.querySelector('[data-provider="openai"]').disabled`);
 assert(!(await state()).providers[0].enabled,'Gateway start must not enable a supplier');
 await browser('click','[data-provider="openai"]');
 await wait(`document.querySelector('[data-provider="openai"]').getAttribute('aria-checked')==='true' && !document.querySelector('[data-power]').disabled`);
 assert.equal(await browser('get','count','.adaptor-feedback.success').then(r=>r.stdout.trim()),'0');
 let current=await state();assert(current.running&&current.providers[0].enabled);
 assert((await(await fetch(base+'/api/snapshot')).json()).gateway);
 assert.notEqual(current.port,oldPort,'Occupied preferred port must trigger automatic fallback');
 assert.equal(current.providers[0].base_url,`http://127.0.0.1:${current.port}/openai`);
 assert.equal(await (await fetch(`http://127.0.0.1:${oldPort}/__corki_gateway/health`)).text(),'foreign-fixture');
 await wait(`!document.querySelector('#adaptor-workspace input') && !document.querySelector('[data-port-save]') && document.querySelector('[data-gateway-port]').textContent.trim()==='${current.port}'`);
 await wait(`!document.querySelector('.adaptor-provider-footer') && !document.querySelector('.adaptor-profile-name') && document.querySelector('.adaptor-provider').getBoundingClientRect().width<=340`);
 await browser('screenshot',path.join(os.tmpdir(),'corki-adaptor-enabled.png'));
 await browser('eval',`location.hash='#models'`);
 await wait(`document.querySelector('[data-provider-group="system"]') && !document.querySelector('[data-provider-group="system"]').open`);
 await wait(`!document.querySelector('[data-action="provider-toggle"][data-id="adaptor-openai"]')`);
 await browser('eval',`location.hash='#adaptor'`);
 await wait(`document.querySelector('[data-provider="openai"]')`);
 assert((await state()).providers[0].enabled);
 await browser('click','[data-provider="openai"]');
 await wait(`document.querySelector('[data-provider="openai"]').getAttribute('aria-checked')==='false' && !document.querySelector('[data-power]').disabled`);
 assert((await state()).running,'Disabling the last supplier must leave the gateway running');
 await browser('click','[data-power]');
 await wait(`document.querySelector('[data-power]').textContent==='开启网关' && document.querySelector('[data-provider="openai"]').disabled`);
 assert(!(await state()).running);
 await browser('eval',`location.hash='#models'`);
 await wait(`document.querySelector('[data-provider-group="system"]') && !document.querySelector('[data-provider-group="system"] .provider-card')`);
 await browser('eval',`location.hash='#adaptor'`);
 await wait(`document.querySelector('[data-provider="openai"]')`);
 await browser('click','[data-power]');
 await wait(`document.querySelector('[data-power]').textContent==='关闭网关' && !document.querySelector('[data-provider="openai"]').disabled`);
 const authFile=path.join(path.dirname(initial.path),'fixture-auth.json'),savedAuth=await readFile(authFile);
 assert(savedAuth.toString().includes('fixture-token'),'Fake credential fixture required');
 const beforeFailure=await(await fetch(base+'/api/config')).json();
 try{
  await writeFile(authFile,JSON.stringify({tokens:{}}));
  await browser('click','[data-provider="openai"]');
  await wait(`document.querySelector('#toast.visible')?.textContent==='开启失败，没有检测到对应登录信息' && !document.querySelector('.adaptor-progress')`);
  assert.equal(await browser('get','count','.adaptor-feedback').then(r=>r.stdout.trim()),'0');
  assert((await state()).running && !(await state()).providers[0].enabled);
  assert.equal((await(await fetch(base+'/api/config')).json()).revision,beforeFailure.revision);
 }finally{await writeFile(authFile,savedAuth);}
 const snapshot=await (await fetch(base+'/api/snapshot')).json();
 const response=await fetch(base+'/api/mutate',{method:'POST',headers:{'Content-Type':'application/json','X-Corki-Manager':'1'},body:JSON.stringify({kind:'providers',action:'save',id:'adaptor-claude',revision:snapshot.revision,values:{id:'adaptor-claude',base_url:'https://api.example.invalid',api_mode:'responses'}})});
 assert.equal(response.status,400);assert.equal((await response.json()).error,"供应商名称不能以'adaptor-'开头");
 console.log('PASS enable/configure, automatic fallback preserves foreign listener and updates profile, model-management toggles removed, power-off retains config, reserved prefix rejection');
}finally{
 const current=await state();
 if(current.running)await fetch(base+'/api/mutate',{method:'POST',headers:{'Content-Type':'application/json','X-Corki-Manager':'1'},body:JSON.stringify({kind:'adaptor',action:'power',enabled:false,revision:current.revision})});
 await browser('close').catch(()=>{});
}
