// Isolated fixture only. No real contacts, models or gateway connections are used.
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
const base=process.env.MANAGER_TEST_URL;assert(base);
const cfg=await(await fetch(base+'/api/config')).json();assert(cfg.path.includes('corki-channel-recall-qa-'));
const state=await(await fetch(base+'/api/channel-gateway')).json(),account=state.connections[0];
const run=promisify(execFile),session='channel-reset-'+process.pid;
const browser=(...args)=>run('agent-browser',['--session',session,...args],{maxBuffer:1024*1024});
const value=async code=>JSON.parse((await browser('eval',code)).stdout.trim());
const wait=code=>browser('wait','--fn',code);
try{
 await browser('open',base+'/#channels/'+account.id);await browser('set','viewport','1440','900');
 await wait(`!!document.querySelector('[data-reset-current]') && !document.querySelector('[data-reset-current]').disabled`);
 assert.equal(await value(`document.querySelector('[data-reset-current]').hidden`),false);
 assert.equal(await value(`getComputedStyle(document.querySelector('[data-reset-conversation]')).backgroundColor`),'rgb(195, 71, 59)');
 const before=await value(`document.querySelector('.channel-chat-messages').textContent`);
 await browser('click','[data-reset-current]');
 await wait(`!!document.querySelector('.channel-dialog')`);
 assert((await value(`document.querySelector('.channel-dialog-body').textContent`)).includes('其他用户不受影响'));
 await browser('click','.channel-dialog-foot [data-close]');
 assert.equal(await value(`document.querySelector('.channel-chat-messages').textContent`),before);
 await browser('click','[data-reset-current]');await browser('click','.channel-dialog [type=submit]');
 await wait(`!document.querySelector('.channel-dialog') && !document.querySelector('[data-reset-current]').disabled`);
 assert.equal(await value(`document.querySelector('.channel-chat-messages').textContent`),before);
 await browser('screenshot',join(tmpdir(),'corki-reset-current.png'));
 await browser('click','[data-channel-tab="settings"]');
 assert.equal(await value(`!!document.querySelector('[name="session_mode"]')`),false);
 console.log('PASS independent reset, confirmation/cancel, transcript preservation, red reset-all, no session-mode setting');
}finally{await browser('close').catch(()=>{});}
