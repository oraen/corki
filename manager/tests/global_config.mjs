// Requires agent-browser and a disposable manager started with a corki-global-config-qa-* home.
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import os from 'node:os';
import path from 'node:path';
const base=process.env.MANAGER_TEST_URL;
assert(base,'Set MANAGER_TEST_URL to an isolated manager');
const config=await (await fetch(base+'/api/config')).json();
assert(config.path.includes('corki-global-config-qa-'),'Disposable home required');
const run=promisify(execFile),session='corki-global-settings-'+process.pid;
const browser=(...args)=>run('agent-browser',['--session',session,...args],{maxBuffer:1024*1024});
try{
 await browser('open',base);
 await browser('eval',`(async()=>{for(let i=0;i<200;i++){if(document.querySelectorAll('.global-config-panel [role=switch]').length===4)return true;await new Promise(r=>setTimeout(r,25))}throw Error('Global configuration did not load')})()`);
 await browser('snapshot','-i');
 const initial=await (await fetch(base+'/api/snapshot')).json();
 if(initial.global_config.sound_notifications){await browser('click', '[data-key="sound_notifications"]');await browser('eval', `(async()=>{while(document.querySelector('[data-key="sound_notifications"]').disabled)await new Promise(r=>setTimeout(r,25))})()`);}
 for(const enabled of [true,false]){
  for(const key of ['self_evolution','memories','auto_cleanup','sound_notifications']){
   await browser('click',`.global-config-panel [data-key="${key}"]`);
   await browser('eval',`(async()=>{for(let i=0;i<120;i++){const button=document.querySelector('[data-key="${key}"]');if(button && !button.disabled && button.getAttribute('aria-checked')==='${enabled}')return true;await new Promise(r=>setTimeout(r,25))}throw Error('Switch did not persist: ${key}')})()`);
   const state=await (await fetch(base+'/api/snapshot')).json();
   assert.equal(state.global_config[key],enabled);
  }
 }
 for(const value of ['read-only','full','workspace']){
  await browser('click','[data-default-field="permissions"] .select-trigger');
  await browser('click',`[data-default-field="permissions"] [data-value="${value}"]`);
  await browser('eval',`(async()=>{for(let i=0;i<120;i++){const root=document.querySelector('[data-default-field="permissions"]');if(root?.querySelector('input').value==='${value}' && !root.querySelector('.select-trigger').disabled)return true;await new Promise(r=>setTimeout(r,25))}throw Error('Permission did not persist')})()`);
  const snapshot=await(await fetch(base+'/api/snapshot')).json();
  assert.equal(snapshot.global_config.permissions,value);
 }
 await browser('reload');
 await browser('eval',`(async()=>{for(let i=0;i<120;i++){const buttons=[...document.querySelectorAll('.global-config-panel [role=switch]')];if(buttons.length===4 && buttons.every(b=>b.getAttribute('aria-checked')==='false'))return true;await new Promise(r=>setTimeout(r,25))}throw Error('Reload did not restore settings')})()`);
 await browser('eval',`document.querySelector('.global-config-panel').scrollIntoView({block:'center'})`);
 await browser('screenshot',path.join(os.tmpdir(),'corki-global-settings.png'));
 const settings=JSON.parse((await (await fetch(base+'/api/config')).json()).content);
 assert.equal(settings.default_permissions,':workspace');
 assert.equal(settings.approval_policy,'on-request');
 assert.equal(settings.memories.enabled,false);
 assert.equal(settings.sessions.auto_cleanup,false);
 assert.equal(settings.features.self_evolution,false);
 assert.equal(settings.tui.notifications,false);
 console.log('PASS four switches and permission dropdown persist immediately, permissions and memory reach config, reload preserves states');
}finally{await browser('close').catch(()=>{});}
