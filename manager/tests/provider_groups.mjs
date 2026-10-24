// Requires agent-browser and a corki-readonly-provider-qa-* disposable manager.
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import path from 'node:path';
import os from 'node:os';
const base=process.env.MANAGER_TEST_URL;
assert(base);
const before=await(await fetch(base+'/api/config')).json();
assert(before.path.includes('corki-readonly-provider-qa-'),'Disposable config required');
const snapshot=await(await fetch(base+'/api/snapshot')).json();
assert.deepEqual(snapshot.default,{provider:'z-first',model:'first-model'});
const exec=promisify(execFile),session='corki-provider-groups-'+process.pid;
const browser=(...args)=>exec('agent-browser',['--session',session,...args],{maxBuffer:1024*1024});
async function wait(expression){await browser('eval',`(async()=>{for(let i=0;i<120;i++){if(${expression})return true;await new Promise(r=>setTimeout(r,25))}throw Error('Provider UI assertion failed')})()`)}
try{
 await browser('open',base+'/#models');
 await wait(`document.querySelectorAll('[data-provider-group]').length===2`);
 await browser('snapshot','-i');
 await wait(`document.querySelectorAll('[data-provider-group="custom"] .provider-card').length===2 && document.querySelectorAll('[data-provider-group="system"] .provider-card').length===1`);
 await wait(`document.querySelector('[data-provider-group="system"] .provider-card-name').textContent==='adaptor-openai' && document.querySelector('.provider-card.is-default .provider-card-name').textContent==='z-first'`);
 await wait(`!document.querySelector('[data-provider-group="system"]').open && !document.querySelector('[data-provider-group="system"] [data-action="provider-toggle"]') && !document.querySelector('[data-id="adaptor-closed"]')`);
 await browser('click','[data-provider-group="system"] > summary');
 await browser('click','[data-provider-group="system"] [data-action="edit"]');
 await wait(`document.querySelector('#editor').open && document.querySelector('#save').disabled`);
 await wait(`[...document.querySelectorAll('#edit-form input')].every(input=>input.readOnly) && document.querySelector('#edit-form .select-trigger').disabled`);
 await browser('eval',`(()=>{const dialog=document.querySelector('#editor').getBoundingClientRect(),save=document.querySelector('#save').getBoundingClientRect();if(dialog.right-save.right<15 || dialog.right-save.right>45)throw Error('Readonly footer is not aligned right')})()`);
 await browser('hover','.adaptor-save-hint');
 await wait(`getComputedStyle(document.querySelector('.adaptor-save-hint'),'::after').opacity==='1' && document.querySelector('.adaptor-save-hint').dataset.tooltip==='适配模型不允许编辑'`);
 await browser('screenshot',path.join(os.tmpdir(),'corki-adaptor-readonly.png'));
 await browser('click','.secret-toggle');
 await wait(`document.querySelector('#provider-key').type==='text' && document.querySelector('#provider-key').value==='fixture-local-key' && document.querySelector('#provider-key').readOnly`);
 await browser('eval',`document.querySelector('#edit-form').requestSubmit()`);
 assert.equal((await(await fetch(base+'/api/config')).json()).revision,before.revision);
 await browser('click','#cancel-dialog');
 await browser('click','[data-action="edit"][data-id="z-first"]');
 await wait(`!document.querySelector('#save').disabled && !document.querySelector('.adaptor-save-hint') && !document.querySelector('input[name=id]').readOnly && !document.querySelector('.select-trigger').disabled`);
 await browser('click','#cancel-dialog');
 await browser('screenshot',path.join(os.tmpdir(),'corki-provider-groups.png'));
 await browser('click','[data-provider-group="system"] > summary');
 await wait(`!document.querySelector('[data-provider-group="system"]').open`);
 await browser('eval',`location.hash='#overview'`);
 await wait(`document.querySelector('.quick-tools-panel')`);
 await browser('eval',`location.hash='#models'`);
 await wait(`document.querySelector('[data-provider-group="system"]') && !document.querySelector('[data-provider-group="system"]').open`);
 assert.equal((await(await fetch(base+'/api/config')).json()).revision,before.revision);
 console.log('PASS first implicit default, provider groups and collapse state, managed read-only controls and tooltip, reveal/copy-friendly key, regular form reset; no config writes');
}finally{await browser('close').catch(()=>{});}
