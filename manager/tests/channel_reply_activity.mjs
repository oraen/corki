// Isolated manager + deterministic runtime activity; no real platform messages.
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
const base=process.env.MANAGER_TEST_URL;assert(base);
const cfg=await(await fetch(base+'/api/config')).json();
assert(cfg.path.includes('corki-channel-reply-qa-'));
const snapshot=await(await fetch(base+'/api/channel-gateway')).json();
const account=snapshot.connections.find(c=>c.name==='回复状态测试');assert(account);
const run=promisify(execFile),session='channel-reply-'+process.pid;
const browser=(...args)=>run('agent-browser',['--session',session,...args],{maxBuffer:1024*1024});
const value=async code=>JSON.parse((await browser('eval',code)).stdout.trim());
const wait=async code=>browser('wait','--fn',code);
const click=selector=>browser('click',selector);
try {
 await browser('open',base+'/#channels/'+account.id);await browser('set','viewport','1440','900');
 await wait(`!!document.querySelector('[data-channel-tab="applications"]')`);
 await value(`(()=>{
  window.replyFixture={activity:{alice:{message_ids:['turn-1'],stopping:false}},calls:[],toasts:[],fail:false};
  const fixture=window.replyFixture;
  const api=async(kind,payload)=>{
   if(payload?.action==='stop-reply'){
    fixture.calls.push(payload);
    if(fixture.fail)throw Error('测试连接失败');
    fixture.activity={alice:{message_ids:['turn-1'],stopping:true}};
    return {requested:1,reply_activity:fixture.activity};
   }
   const response=await fetch('/api/'+kind,payload?{method:'POST',headers:{'Content-Type':'application/json','X-Corki-Manager':'1'},body:JSON.stringify(payload)}:{});
   const result=await response.json();if(!response.ok)throw Error(result.error);
   if(payload?.action==='applications')result.reply_activity=fixture.activity;
   return result;
  };
  localStorage.setItem('corki.channel.management-tab.'+${JSON.stringify(account.id)},'applications');
  window.CorkiChannels.mount(document.querySelector('#channel-gateway-workspace'),{api,id:${JSON.stringify(account.id)},providers:[],toast:text=>fixture.toasts.push(text)});
  return true;
 })()`);
 await wait(`!!document.querySelector('[data-channel-tab="applications"]')`);
 await click('[data-channel-tab="applications"]');
 await wait(`document.querySelectorAll('.channel-application-table tbody tr').length===3`);
 assert.equal(await value(`document.querySelector('.channel-application-search input').placeholder`),'搜索用户昵称或者ID');
 assert.equal(await value(`document.querySelectorAll('.channel-request-status.replying').length`),1);
 assert.equal(await value(`document.querySelectorAll('.channel-request-status.approved').length`),1);
 await browser('screenshot',join(tmpdir(),'corki-reply-status.png'));
 assert.equal(await value(`document.querySelector('[data-stop-reply="alice"]').textContent`),'');
 await click('[data-stop-reply="alice"]');
 await wait(`!!document.querySelector('.channel-stop-dialog')`);
 assert.equal(await value(`document.activeElement.textContent`),'取消');
 await browser('screenshot',join(tmpdir(),'corki-reply-confirm.png'));
 await click('.channel-stop-dialog .channel-dialog-foot [data-close]');
 assert.equal(await value(`replyFixture.calls.length`),0);
 await click('[data-stop-reply="alice"]');
 await value(`replyFixture.fail=true;true`);
 await click('.channel-stop-dialog [type="submit"]');
 await wait(`document.querySelector('.channel-stop-dialog [role="alert"]').textContent==='测试连接失败'`);
 assert.equal(await value(`document.querySelector('.channel-stop-dialog [type="submit"]').disabled`),false);
 await value(`replyFixture.fail=false;true`);
 await click('.channel-stop-dialog [type="submit"]');
 await wait(`!document.querySelector('.channel-stop-dialog')`);
 assert.equal(await value(`document.querySelector('[data-stop-reply="alice"]').disabled`),true);
 assert.equal(await value(`document.querySelector('[data-stop-reply="alice"]').getAttribute('aria-label')`),'终止中');
 assert.deepEqual(await value(`replyFixture.calls.at(-1).message_ids`),['turn-1']);
 await value(`replyFixture.activity={};true`);
 await wait(`!document.querySelector('[data-stop-reply]')`);
 assert.equal(await value(`document.querySelectorAll('.channel-request-status.approved').length`),2);
 for(const [search,expected] of [['staff-alice',1],['小周',1],['不存在',0],['',3]]){
  await browser('fill','.channel-application-search input',search);
  await wait(`document.querySelectorAll('.channel-application-table tbody tr').length===${expected} && !document.querySelector('.channel-applications-content').textContent.includes('正在读取')`);
 }
 await browser('set','viewport','1280','800');
 await value(`replyFixture.activity={alice:{message_ids:['turn-2'],stopping:false}};true`);
 await wait(`!!document.querySelector('[data-stop-reply="alice"]')`);
 assert(await value(`(()=>{const row=document.querySelector('.channel-reply-state'),cell=row.closest('td');return row.scrollWidth<=cell.clientWidth-35})()`));
 await browser('screenshot',join(tmpdir(),'corki-reply-status-1280.png'));
 console.log('PASS reply/idle, confirmation/cancel/error/retry, stopping, polling, nickname and ID search, 1280 layout');
} catch(error) { console.error(await value(`document.querySelector('#channel-gateway-workspace').innerText`)); throw error; } finally {await browser('close').catch(()=>{});}
