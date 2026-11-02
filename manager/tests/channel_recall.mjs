// Disposable fixture + simulated platform acknowledgement; never recalls real messages.
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
const base=process.env.MANAGER_TEST_URL;assert(base);
const config=await(await fetch(base+'/api/config')).json();assert(config.path.includes('corki-channel-recall-qa-'));
const state=await(await fetch(base+'/api/channel-gateway')).json(),account=state.connections[0];
const run=promisify(execFile),session='channel-recall-'+process.pid;
const browser=(...args)=>run('agent-browser',['--session',session,...args],{maxBuffer:1024*1024});
const value=async code=>JSON.parse((await browser('eval',code)).stdout.trim());
const wait=code=>browser('wait','--fn',code);
try {
 await browser('open',base+'/#channels/'+account.id);await browser('set','viewport','1440','900');
 await wait(`!!document.querySelector('.channel-management')`);
 await value(`(()=>{
  window.recallFixture={calls:[],responses:{},fail:true,toasts:[]};const fixture=recallFixture;
  const api=async(kind,payload)=>{
   if(payload?.action==='recall-message') {
    fixture.calls.push(payload);await new Promise(resolve=>{fixture.release=resolve});
    const reply={recall_state:fixture.fail?'failed':'recalled',recall_error:fixture.fail?'fixture expired':'',can_recall:fixture.fail};
    fixture.responses[payload.message_id]=reply;return {message:reply};
   }
   const r=await fetch('/api/'+kind,payload?{method:'POST',headers:{'Content-Type':'application/json','X-Corki-Manager':'1'},body:JSON.stringify(payload)}:{});
   const result=await r.json();if(!r.ok)throw Error(result.error);
   if(payload?.action==='conversation')result.messages=result.messages.map(m=>({...m,...fixture.responses[m.id]}));
   return result;
  };
  CorkiChannels.mount(document.querySelector('#channel-gateway-workspace'),{id:${JSON.stringify(account.id)},api,toast:text=>fixture.toasts.push(text),providers:[]});return true;
 })()`);
 await wait(`document.querySelectorAll('.channel-chat-message').length===3`);
 assert.equal(await value(`document.querySelector('.channel-chat-message.user .channel-message-toolbar').hidden`),true);
 assert.equal(await value(`document.querySelectorAll('.channel-message-recall:enabled').length`),1);
 assert.equal(await value(`getComputedStyle(document.querySelector('.channel-chat-message.assistant .channel-message-toolbar')).opacity`),'0');
 await browser('hover','.channel-chat-message.assistant:last-child');
 await wait(`getComputedStyle(document.querySelector('.channel-chat-message:last-child .channel-message-toolbar')).opacity==='1'`);
 const before=await value(`document.querySelector('.channel-chat-message:last-child .channel-chat-text').getBoundingClientRect().top`);
 await browser('screenshot',join(tmpdir(),'corki-message-recall-hover.png'));
 await browser('click','.channel-message-recall:enabled');
 await wait(`recallFixture.calls.length===1`);
 assert.equal(await value(`document.querySelector('.channel-chat-message:last-child .channel-message-recall').disabled`),true);
 assert.equal(await value(`document.querySelector('.channel-chat-message:last-child .channel-chat-text').getBoundingClientRect().top`),before);
 await value(`recallFixture.release();true`);
 await wait(`!!document.querySelector('.recall-error')`);
 assert((await value(`document.querySelector('.channel-chat-message:last-child .channel-chat-text').textContent`)).includes('支持撤回'));
 await browser('click','.recall-error');await wait(`!!document.querySelector('.channel-dialog')`);
 assert((await value(`document.querySelector('.channel-dialog').textContent`)).includes('fixture expired'));
 await browser('click','.channel-dialog-foot [data-close]');
 await value(`recallFixture.fail=false;true`);
 await browser('hover','.channel-chat-message.assistant:last-child');await browser('click','.channel-message-recall:enabled');
 await wait(`recallFixture.calls.length===2`);await value(`recallFixture.release();true`);
 await wait(`!!document.querySelector('.channel-chat-message.is-recalled')`);
 assert((await value(`document.querySelector('.is-recalled .channel-chat-text').textContent`)).includes('支持撤回'));
 assert.equal(await value(`document.querySelector('.is-recalled .channel-message-status').textContent`),'已撤回');
 assert.equal(await value(`getComputedStyle(document.querySelector('.is-recalled .channel-message-status')).color`),'rgb(195, 71, 59)');
 assert.equal(await value(`document.querySelector('.is-recalled .channel-message-toolbar').hidden`),true);
 await browser('screenshot',join(tmpdir(),'corki-message-recalled.png'));
 console.log('PASS own-message hover, missing receipt, no layout jump, pending, error details, retry and recalled state');
} finally {await browser('close').catch(()=>{});}
