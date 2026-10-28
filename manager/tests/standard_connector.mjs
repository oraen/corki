// Disposable manager only; HTTP endpoints are illustrative and accounts stay disabled.
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
const base=process.env.MANAGER_TEST_URL;
assert(base);
const config=await (await fetch(base+'/api/config')).json();
assert(/channel.*(?:qa|research)|gateway.*qa/.test(config.path));
const run=promisify(execFile),session='standard-http-ui-'+process.pid;
const browser=(...args)=>run('agent-browser',['--session',session,...args],{maxBuffer:1024*1024});
const wait=expr=>browser('eval',`(async()=>{for(let i=0;i<200;i++){if(${expr})return true;await new Promise(r=>setTimeout(r,30))}throw Error('condition failed')})()`);
const click=async selector=>{await browser('eval',`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center'})`);await browser('click',selector);};
const snapshot=async()=>await(await fetch(base+'/api/channel-gateway')).json();
const nonce=Date.now(),plain='HTTP plain '+nonce,template='HTTP template '+nonce,literal='HTTP literal '+nonce;
try {
  await browser('open',base+'/#channels');
  await wait(`document.querySelector('[data-act="standard"]')`);
  assert((await browser('get','text','[data-act="import"]')).stdout.includes('自定义连接器'));
  for(const [name,url,disabled] of [[plain,'http://127.0.0.1:18080/questions',false],[template,'http://127.0.0.1:18080/questions?account=#{account}',false],[literal,'http://127.0.0.1:18080/questions?value=#{literal}',true]]) {
    await click('[data-act="standard"]');
    await browser('fill','[name="name"]',name);
    await browser('fill','[name="pull_url"]',url);
    await browser('fill','[name="reply_url"]','http://127.0.0.1:18080/replies');
    if(disabled) await click('[name="disable_parameters"]');
    if(name===plain) {
      await click('.standard-guide-trigger');
      await wait(`document.querySelector('.standard-protocol .cm-editor')`);
      await click('.standard-guide-trigger');
      assert((await browser('get','value','[name="name"]')).stdout.includes(plain));
    }
    await click('.channel-dialog [type="submit"]');
    await wait(`!document.querySelector('.channel-dialog')`);
  }
  let state=await snapshot();
  for(const name of [plain,literal]) {
    const channel=state.channels.find(c=>c.name===name);
    const accounts=state.connections.filter(c=>c.channel===channel.id);
    assert.equal(accounts.length,1);assert.equal(accounts[0].enabled,false);
  }
  const type=state.channels.find(c=>c.name===template);
  assert.equal(state.connections.filter(c=>c.channel===type.id).length,0);
  await click('[data-act="add-connection"]');
  await click('.channel-select summary');
  await click(`[data-option="${type.id}"]`);
  await click('.channel-dialog-foot .primary');
  await browser('fill','[name="_name"]','Parameterized account '+nonce);
  await browser('fill','[name="param_account"]','demo user');
  await click('.channel-dialog [type="submit"]');
  await wait(`!document.querySelector('.channel-dialog')`);
  state=await snapshot();
  const account=state.connections.find(c=>c.name==='Parameterized account '+nonce);
  assert(account.secrets.param_account);assert(!JSON.stringify(state).includes('demo user'));
  await click(`[data-config="${account.id}"]`);
  await wait(`document.querySelector('.channel-management')`);
  await click('[data-disable-parameters]');
  await wait(`document.querySelector('[name="param_account"]').disabled`);
  await click('[data-page-save]');
  await wait(`document.querySelector('[data-page-save]') && !document.querySelector('[data-page-save]').disabled`);
  await wait(`document.querySelector('[data-disable-parameters]').checked`);
  assert.equal((await snapshot()).connections.find(c=>c.id===account.id).values.http_disable_parameters,'true');
  await click('.header-back');
  await wait(`document.querySelector('[data-act="standard"]')`);
  assert.equal((await(await fetch(base+'/api/config')).json()).revision,config.revision);
  console.log('PASS standard connector form, protocol viewer, no-parameter/literal auto account, parameter creation, secret redaction, per-account disable, core settings unchanged');
} finally {
  await browser('close').catch(()=>{});
}
