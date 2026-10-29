// Remember only UI tab names; isolated profile, no channel transport or model calls.
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {dirname} from 'node:path';
const base=process.env.MANAGER_TEST_URL;assert(base);
const cfg=await(await fetch(base+'/api/config')).json();
assert(/channel.*(?:qa|research)/.test(cfg.path));
const run=promisify(execFile),session='channel-tab-state-'+process.pid;
const browser=(...args)=>run('agent-browser',['--session',session,...args],{maxBuffer:1024*1024});
const value=async code=>JSON.parse((await browser('eval',code)).stdout.trim());
const wait=async code=>{for(let i=0;i<60;i++){if(await value(`Boolean(${code})`))return;await new Promise(r=>setTimeout(r,100));}throw Error(code);};
const result=await run('.venv/bin/python',['-c',`
import sys,json
from pathlib import Path
sys.path.insert(0,'gateway/src')
from corki_gateway.store import Store
s=Store(Path(sys.argv[1]))
print(json.dumps([s.save_connection({'channel':'dingtalk','name':'Tab fixture '+str(i),'values':{'session_validation':'true' if i==0 else 'false'}}) for i in range(2)]))
`,dirname(cfg.path)]);
const [one,two]=JSON.parse(result.stdout);
const current=tab=>wait(`document.querySelector('[data-channel-tab="${tab}"]')?.getAttribute('aria-selected')==='true'`);
const navigate=async id=>{await browser('eval',`location.hash=${JSON.stringify(id?'channels/'+id:'channels')};true`);};
try {
 await browser('open',base+'/#channels/'+one);await current('history');
 await browser('click','[data-channel-tab="applications"]');await current('applications');
 await navigate(two);await current('history');
 await browser('click','[data-channel-tab="settings"]');await current('settings');
 await navigate(one);await current('applications');
 await navigate(null);await wait(`document.querySelector('[data-config="${two}"]')`);
 await browser('click',`[data-config="${two}"]`);await current('settings');
 await browser('reload');await current('settings');
 await navigate(one);await current('applications');
 // A previously remembered Applications tab is unavailable when validation is off.
 await browser('eval',`localStorage.setItem('corki.channel.management-tab.${two}','applications');true`);
 await browser('open',base+'/#channels/'+two);await current('settings');
 await browser('eval',`localStorage.setItem('corki.channel.management-tab.${two}','invalid');true`);
 await browser('reload');await current('history');
 console.log('PASS independent per-connection tabs, leave/re-enter, reload persistence, hidden-tab fallback, invalid stored value');
}finally{await browser('close').catch(()=>{});}
