// Disposable manager only. No real model requests or channel connections.
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {dirname} from 'node:path';
const base=process.env.MANAGER_TEST_URL;assert(base);
const initial=await(await fetch(base+'/api/config')).json();
assert(/channel.*(?:qa|research)/.test(initial.path));
const run=promisify(execFile),session='channel-models-'+process.pid;
const browser=(...args)=>run('agent-browser',['--session',session,...args],{maxBuffer:1024*1024});
const evaluate=async code=>(await browser('eval',code)).stdout.trim();
const wait=async expr=>{for(let i=0;i<50;i++){if(await evaluate(`Boolean(${expr})`)==='true')return;await new Promise(r=>setTimeout(r,150));}throw Error(expr);};
const click=async selector=>{await evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center'})`);await browser('click',selector);};
const seeded=await run('.venv/bin/python',['-c',`
import sys,json
from pathlib import Path
sys.path.insert(0,'gateway/src')
from corki_gateway.store import Store
home=Path(sys.argv[1]);path=home/'settings.json';doc=json.loads(path.read_text())
profile={'base_url':'http://127.0.0.1:1/v1','api_mode':'chat_completions','models':{'small':{},'large':{}},'default_model':'large'}
doc['providers']={'Fixture One':profile,'Fixture Two':{**profile,'default_model':'small'}}
path.write_text(json.dumps(doc));s=Store(home)
i=s.save_connection({'channel':'wechat','name':'Model UI fixture '+str(__import__('time').time_ns()),'values':{}})
print(json.dumps({'id':i,'binding':s.connection(i)['conversation_id']}))
`,dirname(initial.path)]);
const fixture=JSON.parse(seeded.stdout);
const baseline=await(await fetch(base+'/api/config')).json();
try {
 await browser('open',base+'/#channels/'+fixture.id);
 await browser('set','viewport','1440','900');
 await wait(`document.querySelector('.channel-agent-provider')`);
 assert.equal(await evaluate(`document.querySelector('[name="agent_provider"]').value`),'""');
 await click('[data-channel-tab="settings"]');await click('[data-page-edit]');
 await click('.channel-agent-provider summary');await click('.channel-agent-provider [data-option="Fixture One"]');
 assert.equal(await evaluate(`document.querySelector('[name="agent_model"]').value`),'"large"');
 await click('[data-channel-tab="history"]');
 assert.equal(await evaluate(`document.querySelector('.channel-account-settings').hidden`),'true');
 await click('[data-channel-tab="settings"]');
 assert.equal(await evaluate(`document.querySelector('[name="agent_provider"]').value`),'"Fixture One"');
 assert.equal(await evaluate(`document.querySelector('[data-page-edit]').hidden`),'true');
 await click('.channel-agent-model summary');await click('.channel-agent-model [data-option="small"]');
 await click('[data-page-save]');
 await wait(`document.querySelector('[data-page-edit]') && !document.querySelector('[data-page-edit]').hidden && document.querySelector('[name="agent_provider"]').value==='Fixture One'`);
 await click('[data-channel-tab="settings"]');await click('[data-page-edit]');await click('.channel-agent-provider summary');await click('.channel-agent-provider [data-option="Fixture Two"]');
 assert.equal(await evaluate(`document.querySelector('[name="agent_model"]').value`),'"small"');
 await click('[data-page-cancel]');
 assert.equal(await evaluate(`document.querySelector('[name="agent_provider"]').value`),'"Fixture One"');
 assert.equal(await evaluate(`document.querySelector('[name="agent_model"]').value`),'"small"');
 await browser('reload');await wait(`document.querySelector('[name="agent_provider"]')?.value==='Fixture One'`);
 await click('[data-channel-tab="settings"]');
 if(process.env.CHANNEL_MODEL_SCREENSHOT)await browser('screenshot',process.env.CHANNEL_MODEL_SCREENSHOT);
 await click('[data-channel-tab="settings"]');await click('[data-page-edit]');await click('.channel-agent-provider summary');await click('.channel-agent-provider [data-option=""]');await click('[data-page-save]');
 await wait(`!document.querySelector('[data-page-edit]').hidden && document.querySelector('[name="agent_provider"]').value===''`);
 const status=await(await fetch(base+'/api/channel-gateway')).json();
 const row=status.connections.find(r=>r.id===fixture.id);
 assert.equal(row.values.agent_model,'');assert.equal(row.conversation_id,fixture.binding);
 assert.equal((await(await fetch(base+'/api/config')).json()).revision,baseline.revision);
 console.log('PASS provider/model selection, provider default, cancel, reload, global fallback, binding retained, global settings unchanged');
} finally {await browser('close').catch(()=>{});}
