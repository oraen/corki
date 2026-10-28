// Use an empty disposable manager. Lifecycle calls are real; held responses and
// display fixtures make pending/failure states deterministic without model calls.
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';

const base = process.env.MANAGER_TEST_URL;
assert(base);
const config = await (await fetch(base + '/api/config')).json();
assert(/channel.*(?:qa|research)/.test(config.path));
const action = async body => {
  const response = await fetch(base + '/api/mutate', {method:'POST',
    headers:{'Content-Type':'application/json','X-Corki-Manager':'1'},
    body:JSON.stringify({kind:'channel-gateway',...body})});
  const result = await response.json();
  assert(response.ok, result.error);
  return result;
};
const initial = await (await fetch(base + '/api/channel-gateway')).json();
assert(!initial.running && !initial.connections.some(c => c.enabled));
const run = promisify(execFile), session = 'channel-states-' + process.pid;
const browser = (...args) => run('agent-browser',['--session',session,...args],{maxBuffer:1024*1024});
const evaluate = code => browser('eval',code);
const wait = async expr => {
  for(let i=0;i<40;i++) {
    if((await evaluate(`Boolean(${expr})`)).stdout.trim()==='true') return;
    await new Promise(resolve=>setTimeout(resolve,250));
  }
  throw Error('condition failed: '+expr);
};
const check = expr => evaluate(`if(!(${expr}))throw Error(${JSON.stringify(expr)})`);
const click = async selector => {
  await evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center'})`);
  await browser('click',selector);
};
let account;
try {
  const state = await action({action:'create-standard',name:'Delivery fixture '+Date.now(),
    pull_url:'http://127.0.0.1:1/questions',reply_url:'http://127.0.0.1:1/replies'});
  account = state.connections.find(c=>!initial.connections.some(i=>i.id===c.id));
  assert(account && !account.enabled);
  await browser('open',base+'/#channels');
  await wait(`document.querySelector('[data-act="power"]')`);
  await evaluate(`window.testPowerCalls=0; window.testFailed=false; window.testHistoryCalls=0;
    const original=window.fetch.bind(window);
    window.fetch=async (url,opts)=>{
      const body=opts?.body ? JSON.parse(opts.body) : {};
      if(body.kind==='channel-gateway' && body.action==='power'){
        window.testPowerCalls++;
        await new Promise(resolve=>window.releaseTestPower=resolve);
        if(window.failTestPower){window.failTestPower=false;throw Error('测试启动失败')}
      }
      if(body.kind==='channel-gateway' && body.action==='conversation' && body.id===${JSON.stringify(account.id)}){
        window.testHistoryCalls++;
        return new Response(JSON.stringify({thread:'fixture-thread',before:null,active:false,messages:[
          {id:'q1',role:'user',content:'请汇总今天的任务',created_at:'2026-01-01T08:00:00Z'},
          {id:'a1',role:'assistant',content:'已完成汇总，回答保存在这里。',created_at:'2026-01-01T08:01:00Z',delivery_failed:window.testFailed}
        ]}),{headers:{'Content-Type':'application/json'}});
      }
      return original(url,opts);
    };`);
  await click('[data-act="power"]');
  await check(`document.querySelector('.channel-power [role="status"]').textContent==='开启中' && document.querySelector('.channel-power .spinner') && document.querySelector('[data-act="power"]').disabled && !document.querySelector('.channel-progress')`);
  await click('[data-act="standard"]');
  await browser('fill','[name="name"]','仍可编辑');
  await browser('press','Escape');
  await click('.header-back');
  await wait(`document.querySelector('[data-action="open-channels"]')`);
  await click('[data-action="open-channels"]');
  await wait(`document.querySelector('.channel-power .pending')`);
  await check(`document.querySelector('[data-act="power"]').disabled && window.testPowerCalls===1`);
  if(process.env.CHANNEL_STATE_SCREENSHOT) await browser('screenshot',process.env.CHANNEL_STATE_SCREENSHOT);
  await evaluate('window.releaseTestPower()');
  await wait(`document.querySelector('.channel-power .online') && !document.querySelector('[data-act="power"]').disabled`);
  assert.equal((await(await fetch(base+'/api/channel-gateway')).json()).running,true);
  await click(`[data-config="${account.id}"]`);
  await wait(`document.querySelector('[data-id="a1"]')`);
  await check(`document.querySelector('[data-id="a1"] .channel-reply-failed').hidden`);
  await evaluate(`window.originalAnswer=document.querySelector('[data-id="a1"] .channel-chat-text');window.testFailed=true`);
  // Background tabs deliberately suspend polling; activate the test's own tab.
  await browser('tab','t1');
  await check('!document.hidden');
  await wait(`!document.querySelector('[data-id="a1"] .channel-reply-failed').hidden`);
  await check(`window.originalAnswer===document.querySelector('[data-id="a1"] .channel-chat-text') && !document.querySelector('.channel-queue-alert:not([hidden])')`);
  if(process.env.CHANNEL_REPLY_SCREENSHOT) await browser('screenshot',process.env.CHANNEL_REPLY_SCREENSHOT);
  await click('.header-back');
  await wait(`document.querySelector('[data-act="power"]')`);
  await click('[data-act="power"]');
  await check(`document.querySelector('.channel-power [role="status"]').textContent==='关闭中' && !document.querySelector('[data-act="standard"]').disabled && !document.querySelector('.channel-progress')`);
  await evaluate('window.releaseTestPower()');
  await wait(`document.querySelector('.channel-power [role="status"]').textContent==='已关闭' && !document.querySelector('[data-act="power"]').disabled`);
  assert.equal((await(await fetch(base+'/api/channel-gateway')).json()).running,false);
  await evaluate('window.failTestPower=true');
  await click('[data-act="power"]');
  await evaluate('window.releaseTestPower()');
  await wait(`!document.querySelector('[data-act="power"]').disabled`);
  await check(`document.querySelector('.channel-power [role="status"]').textContent==='已关闭' && !document.querySelector('.channel-power .spinner')`);
  assert.equal((await(await fetch(base+'/api/config')).json()).revision,config.revision);
  console.log('PASS real gateway start/stop, nonblocking pending UI, navigation, duplicate guard, failure recovery, reply badge polling without replacing text, core settings unchanged');
} catch(error) {
  console.error((await evaluate(`JSON.stringify({hidden:document.hidden,historyCalls:window.testHistoryCalls,failed:window.testFailed,alerts:document.querySelector('.channel-history-error')?.textContent})`)).stdout);
  throw error;
} finally {
  await action({action:'power',enabled:false});
  if(account) await action({action:'delete-connection',id:account.id});
  await browser('close').catch(()=>{});
}
