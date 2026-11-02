// An isolated manager with generated image assets, never real user messages.
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import os from 'node:os';
import path from 'node:path';
const base=process.env.MANAGER_TEST_URL;assert(base);
assert((await(await fetch(base+'/api/config')).json()).path.includes('corki-channel-message-qa-'));
const run=promisify(execFile),session='channel-images-'+process.pid;
const browser=(...args)=>run('agent-browser',['--session',session,...args],{maxBuffer:1024*1024});
const value=async code=>JSON.parse((await browser('eval',code)).stdout.trim());
const wait=async code=>{for(let i=0;i<60;i++){if(await value(`Boolean(${code})`))return;await new Promise(r=>setTimeout(r,100));}throw Error(code);};
try{
 const status=await(await fetch(base+'/api/channel-gateway')).json();
 const id=status.connections.find(c=>c.channel==='dingtalk').id;
 await browser('open',base+'/#channels/'+id);await browser('set','viewport','1440','1000');
 await wait(`document.querySelector('[data-channel-tab="history"]')`);
 await browser('click','[data-channel-tab="history"]');
 await wait(`document.querySelector('[data-session]')`);
 const alice=await value(`[...document.querySelectorAll('[data-session]')].find(b=>b.textContent.trim()==='Alice').dataset.session`);
 await browser('click',`[data-session="${alice}"]`);
 await wait(`document.querySelector('.channel-image-thumb img')?.naturalWidth>0`);
 assert.equal(await value(`[...document.querySelectorAll('.channel-chat-text')].some(n=>n.textContent.includes('[Image #'))`),false);
 const thumb=await value(`document.querySelector('.channel-image-thumb img').getAttribute('src')`);
 const response=await fetch(base+thumb);assert.equal(response.status,200);assert.equal(response.headers.get('content-type'),'image/jpeg');
 const forbidden=new URL(base+thumb);forbidden.searchParams.set('channel','missing-channel');
 assert.notEqual((await fetch(forbidden)).status,200);
 await browser('click','.channel-image-thumb');
 await wait(`document.querySelector('.channel-original-image')?.naturalWidth===600`);
 await browser('click','.channel-dialog-foot [data-close]');
 await browser('click','.channel-image-placeholder:not(:disabled)');
 assert((await value(`document.querySelector('.channel-image-error').textContent`)).includes('HTTP 403'));
 await browser('click','.channel-dialog-foot [data-close]');
 await browser('reload');await wait(`document.querySelector('[data-session]')`);
 await browser('click',`[data-session="${alice}"]`);await wait(`document.querySelector('.channel-image-thumb img')?.naturalWidth>0`);
 await browser('screenshot',path.join(os.tmpdir(),'corki-channel-images.png'));
 console.log('PASS thumbnails, original preview, failure detail, asset ownership, Unicode markers and reload');
}finally{await browser('close').catch(()=>{});}
