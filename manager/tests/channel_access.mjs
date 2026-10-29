// Disposable manager profile only. No real users, credentials or platform requests.
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {dirname} from 'node:path';
const base=process.env.MANAGER_TEST_URL;assert(base);
const cfg=await(await fetch(base+'/api/config')).json();
assert(/channel.*(?:qa|research)/.test(cfg.path));
const run=promisify(execFile),session='channel-access-'+process.pid;
const browser=(...args)=>run('agent-browser',['--session',session,...args],{maxBuffer:1024*1024});
const value=async code=>JSON.parse((await browser('eval',code)).stdout.trim());
const wait=async code=>{for(let i=0;i<60;i++){if(await value(`Boolean(${code})`))return;await new Promise(r=>setTimeout(r,100));}throw Error(code);};
const click=async selector=>{await value(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center'});true`);await browser('click',selector);};
await run('.venv/bin/python',['-c',`
import sys
from pathlib import Path
home=Path(sys.argv[1]);marker=home/'gateway/node/dingtalk/.installed';marker.parent.mkdir(parents=True,exist_ok=True);marker.write_text('1')
`,dirname(cfg.path)]);
const name='Access fixture '+Date.now();
try {
 await browser('open',base+'/#channels');await browser('set','viewport','1440','900');
 await wait(`document.querySelector('[data-act="add-connection"]')`);
 await click('[data-act="add-connection"]');await click('.channel-dialog-foot .primary');
 await wait(`document.querySelector('.channel-connect-dialog')`);
 assert.equal(await value(`document.querySelector('[name="session_validation"]').checked`),true);
 await browser('fill','[name="_name"]',name);
 await browser('fill','[name="client_id"]','fixture-id');
 await browser('fill','[name="client_secret"]','fixture-secret');
 assert.equal(await value(`document.querySelector('.channel-validation-replies').hidden`),false);
 assert.equal(await value(`document.querySelector('[name="validation_approved_reply"]').value`),'管理员已为您开通权限，现在可以开始对话了');
 await browser('fill','[name="validation_first_reply"]','你好，你的申请已提交，请稍候。');
 if(process.env.CHANNEL_ACCESS_FORM_SCREENSHOT)await browser('screenshot',process.env.CHANNEL_ACCESS_FORM_SCREENSHOT);
 await click('.channel-dialog [type="submit"]');await wait(`!document.querySelector('.channel-dialog')`);
 const snapshot=await(await fetch(base+'/api/channel-gateway')).json();
 const account=snapshot.connections.find(c=>c.name===name);assert(account);
 assert.equal(account.values.session_validation,'true');
 assert.equal(account.values.validation_first_reply,'你好，你的申请已提交，请稍候。');
 await run('.venv/bin/python',['-c',`
import sys
from pathlib import Path
sys.path.insert(0,'gateway/src')
from corki_gateway.store import Store
s=Store(Path(sys.argv[1]));channel=sys.argv[2];row=s.connection(channel)
for sender,name,text,status in [('alice','小林','我想了解如何使用知识库。','pending'),('bob','小周','帮我整理今天的计划。','approved'),('carol','小陈','<img src=x onerror=window.unsafe=true>'+('文本'*200),'disabled')]:
 s.allows_message(row,{'id':'first-'+sender,'sender':sender,'conversation':'chat-'+sender,'name':name,'text':text})
 if status!='pending':s.set_application_status(channel,sender,status)
 if sender=='bob':
  with s.db() as db:db.execute("UPDATE access_delivery SET status='failed',error='回复凭据已过期，请等待用户发送新消息' WHERE channel=? AND sender_id=?",(channel,sender))
`,dirname(cfg.path),account.id]);
 await browser('open',base+'/#channels/'+account.id);
 await wait(`document.querySelector('[data-channel-tab="applications"]')`);
 assert.equal(await value(`document.querySelector('[data-channel-tab="applications"]').hidden`),false);
 await click('[data-channel-tab="applications"]');
 await wait(`document.querySelectorAll('.channel-application-table tbody tr').length===3`);
 assert.equal(await value(`document.querySelectorAll('.channel-request-status.pending').length`),1);
 assert.equal(await value(`document.querySelectorAll('.channel-request-status.approved').length`),1);
 assert.equal(await value(`document.querySelectorAll('.channel-request-status.disabled').length`),1);
 assert.equal(await value(`Boolean(window.unsafe)`),false);
 assert.deepEqual(await value(`Array.from(document.querySelectorAll('.channel-application-table th'),n=>n.textContent)`),['用户 ID','用户名称','请求消息','请求时间','状态','备注','操作']);
 assert.equal(await value(`document.querySelector('[data-notice-error="bob"]').closest('td').cellIndex`),5);
 await click('[data-notice-error="bob"]');
 await wait(`document.querySelector('.channel-diagnostics')`);
 assert((await value(`document.querySelector('.channel-response-body').textContent`)).includes('回复凭据已过期'));
 assert((await value(`document.querySelector('.channel-request-detail').textContent`)).includes('授权通知'));
 await click('.channel-dialog-foot [data-close]');

 if(process.env.CHANNEL_ACCESS_SCREENSHOT)await browser('screenshot',process.env.CHANNEL_ACCESS_SCREENSHOT);
 await click('[data-request-message="carol"]');
 await wait(`document.querySelector('.channel-request-message')`);
 assert((await value(`document.querySelector('.channel-request-message').textContent`)).length>400);
 assert.equal(await value(`document.querySelector('.channel-request-message').children.length`),0);
 await click('.channel-dialog-foot button:last-child');
 await click('[data-request-deny="alice"]');
 await wait(`!document.querySelector('[data-request-deny="alice"]') && document.querySelector('[data-request-action="alice"]')?.textContent==='接入'`);
 for(const expected of ['踢出','接入','踢出']){
   await click('[data-request-action="alice"]');
   await wait(`document.querySelector('[data-request-action="alice"]')?.textContent===${JSON.stringify(expected)}`);
 }
 await browser('reload');await wait(`document.querySelector('[data-channel-tab="applications"]')`);
 await click('[data-channel-tab="applications"]');
 await wait(`document.querySelector('[data-request-action="alice"]')?.textContent==='踢出'`);
 await click('[data-channel-tab="settings"]');
 assert.equal(await value(`document.querySelector('[name="validation_first_reply"]').readOnly`),true);
 await click('[data-page-edit]');
 assert.equal(await value(`document.querySelector('[name="validation_first_reply"]').readOnly`),false);
 await click('[name="session_validation"]');
 assert.equal(await value(`document.querySelector('.channel-validation-replies').hidden`),true);
 await click('[data-page-cancel]');
 assert.equal(await value(`document.querySelector('.channel-validation-replies').hidden`),false);
 assert.equal(await value(`document.querySelector('[name="session_validation"]').checked`),true);
 await click('[data-page-edit]');await click('[name="session_validation"]');await click('[data-page-save]');
 await wait(`document.querySelector('[data-channel-tab="applications"]').hidden && !document.querySelector('[data-page-edit]').hidden`);
 await click('[data-page-edit]');await click('[name="session_validation"]');await click('[data-page-save]');
 await wait(`!document.querySelector('[data-channel-tab="applications"]').hidden && !document.querySelector('[data-page-edit]').hidden`);
 await browser('set','viewport','1280','800');
 if(process.env.CHANNEL_ACCESS_SETTINGS_SCREENSHOT)await browser('screenshot',process.env.CHANNEL_ACCESS_SETTINGS_SCREENSHOT);
 await click('[data-channel-tab="applications"]');await wait(`document.querySelector('[data-request-action="alice"]')?.textContent==='踢出'`);
 assert.equal((await(await fetch(base+'/api/config')).json()).revision,cfg.revision);
 console.log('PASS create default on, pending/approved/disabled, first message escaped/full view, admit/kick/re-admit, persistence, cancel, disable/re-enable, settings unchanged');
} finally {await browser('close').catch(()=>{});}
