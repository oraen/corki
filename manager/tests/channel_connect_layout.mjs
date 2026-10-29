// Isolated gateway UI fixtures. No connector is started and no credential is real.
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {dirname} from 'node:path';
const base=process.env.MANAGER_TEST_URL;assert(base);
const cfg=await(await fetch(base+'/api/config')).json();
assert(/channel.*(?:qa|research)/.test(cfg.path));
const run=promisify(execFile),session='channel-connect-layout-'+process.pid;
const browser=(...args)=>run('agent-browser',['--session',session,...args],{maxBuffer:1024*1024});
const value=async code=>JSON.parse((await browser('eval',code)).stdout.trim());
const wait=async code=>{for(let i=0;i<50;i++){if(await value(`Boolean(${code})`))return;await new Promise(r=>setTimeout(r,100));}throw Error(code);};
await run('.venv/bin/python',['-c',`
import sys,json
from pathlib import Path
home=Path(sys.argv[1]);marker=home/'gateway/node/dingtalk/.installed';marker.parent.mkdir(parents=True,exist_ok=True);marker.write_text('1')
p=home/'settings.json';doc=json.loads(p.read_text());doc['providers']={'Fixture':{'base_url':'http://127.0.0.1:1/v1','models':{f'model-{i}':{} for i in range(30)}}};p.write_text(json.dumps(doc))
`,dirname(cfg.path)]);
const name='Layout fixture '+Date.now();
try {
 await browser('open',base+'/#channels');await browser('set','viewport','1440','900');
 await wait(`document.querySelector('[data-act="add-connection"]')`);
 await browser('click','[data-act="add-connection"]');
 await browser('click','.channel-dialog-foot .primary');
 await wait(`document.querySelector('.channel-connect-dialog')`);
 await browser('fill','[name="_name"]',name);
 await browser('fill','[name="client_id"]','fixture-id');
 await browser('fill','[name="client_secret"]','fixture-secret');
 const geometry=()=>value(`JSON.stringify((()=>{const d=document.querySelector('.channel-connect-dialog'),b=d.querySelector('.channel-dialog-body');return {height:d.offsetHeight,scroll:b.scrollTop,next:d.querySelector('.channel-agent-provider').getBoundingClientRect().top};})())`);
 const before=await geometry();
 await browser('click','.channel-session-mode summary');
 await wait(`document.querySelector('.channel-session-mode [popover]').matches(':popover-open')`);
 assert.equal(await geometry(),before,'opening menu must not move form or scroll');
 await browser('hover','.channel-mode-help');
 await wait(`getComputedStyle(document.querySelector('.channel-mode-help > span')).visibility==='visible'`);
 if(process.env.CHANNEL_CONNECT_SCREENSHOT)await browser('screenshot',process.env.CHANNEL_CONNECT_SCREENSHOT);
 await browser('click','.channel-session-mode [data-option="shared"]');
 assert.equal(await value(`document.querySelector('[name="session_mode"]').value`),'shared');
 await browser('click','.channel-agent-provider summary');await wait(`document.querySelector('.channel-agent-provider [popover]').matches(':popover-open')`);
 await browser('click','.channel-agent-provider [data-option="Fixture"]');
 await browser('click','.channel-agent-model summary');await wait(`document.querySelector('.channel-agent-model [popover]').matches(':popover-open')`);
 await browser('press','Escape');
 await wait(`!document.querySelector('[popover]:popover-open')`);
 assert(await value(`document.querySelector('.channel-connect-dialog').open`),'Escape must close only the menu');
 await browser('click','.channel-session-mode summary');await wait(`document.querySelector('.channel-session-mode [popover]').matches(':popover-open')`);
 await browser('click','[name="_name"]');await wait(`!document.querySelector('[popover]:popover-open')`);
 await browser('set','viewport','1280','650');
 await browser('eval',`document.querySelector('.channel-agent-model summary').scrollIntoView({block:'center'})`);
 const bodyTop=await value(`document.querySelector('.channel-dialog-body').scrollTop`);
 await browser('click','.channel-agent-model summary');await wait(`document.querySelector('.channel-agent-model [popover]').matches(':popover-open')`);
 assert.equal(await value(`document.querySelector('.channel-dialog-body').scrollTop`),bodyTop);
 assert(await value(`(()=>{const r=document.querySelector('.channel-agent-model [popover]').getBoundingClientRect();return r.top>=0 && r.bottom<=innerHeight})()`));
 await browser('press','End');await browser('press','Enter');
 assert.equal(await value(`document.querySelector('[name="agent_model"]').value`),'model-29');
 await browser('click','.channel-dialog [type="submit"]');
 await wait(`!document.querySelector('.channel-dialog')`);
 const status=await(await fetch(base+'/api/channel-gateway')).json();
 const account=status.connections.find(c=>c.name===name);assert(account);
 assert.equal(account.enabled,false);assert.equal(account.values.agent_model,'model-29');
 console.log('PASS stable floating dropdowns, selection, tooltip, click-away, Escape, short-window bounds, keyboard selection, create/save');
} finally {await browser('close').catch(()=>{});}
