// Disposable token fixtures; never send a real platform/model request.
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
const base=process.env.MANAGER_TEST_URL;assert(base);
const cfg=await(await fetch(base+'/api/config')).json();assert(cfg.path.includes('corki-channel-token-qa-'));
const data=await(await fetch(base+'/api/channel-gateway')).json(),account=data.connections[0];
const run=promisify(execFile),session='channel-tokens-'+process.pid;
const browser=(...args)=>run('agent-browser',['--session',session,...args],{maxBuffer:1024*1024});
const value=async code=>JSON.parse((await browser('eval',code)).stdout.trim());
try{
 await browser('open',base+'/#channels/'+account.id);await browser('set','viewport','1440','900');
 await browser('wait','--fn',`!!document.querySelector('[data-channel-tab="applications"]')`);
 await browser('click','[data-channel-tab="applications"]');
 await browser('wait','--fn',`document.querySelectorAll('.channel-token-usage').length===6`);
 assert.deepEqual(await value(`[...document.querySelectorAll('.channel-application-table th')].map(n=>n.textContent)`),['用户 ID','用户昵称','请求消息','请求时间','状态','token用量','备注','操作']);
 assert.deepEqual(await value(`[...document.querySelectorAll('.channel-token-usage')].map(n=>n.textContent)`),['1.3B','1M','12.5K','1K','999','0']);
 assert.equal(await value(`document.querySelector('.channel-token-usage span').getAttribute('aria-label')`),'1,250,000,000 tokens');
 await browser('screenshot',join(tmpdir(),'corki-user-tokens.png'));
 await browser('fill','[aria-label="搜索用户昵称或者ID"]','staff-2');
 await browser('wait','--fn',`document.querySelectorAll('.channel-token-usage').length===1`);
 assert.equal(await value(`document.querySelector('.channel-token-usage').textContent`),'1K');
 console.log('PASS token column order, zero/K/M/B formatting, exact accessible value and search');
}finally{await browser('close').catch(()=>{});}
