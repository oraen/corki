// Disposable manager only. Seed canonical history, never call a model/platform.
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {dirname} from 'node:path';
const base=process.env.MANAGER_TEST_URL;assert(base);
const config=await(await fetch(base+'/api/config')).json();
assert(/channel.*(?:qa|research)/.test(config.path));
const run=promisify(execFile),session='channel-sessions-'+process.pid;
const browser=(...args)=>run('agent-browser',['--session',session,...args],{maxBuffer:1024*1024});
const evaluate=async code=>(await browser('eval',code)).stdout.trim();
const wait=async expr=>{for(let i=0;i<50;i++){if(await evaluate(`Boolean(${expr})`)==='true')return;await new Promise(r=>setTimeout(r,150));}throw Error(expr);};
const click=async selector=>{await evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center'})`);await browser('click',selector);};
const seed=await run('.venv/bin/python',['-c',`
import sys,json,asyncio
from pathlib import Path
sys.path.insert(0,'gateway/src')
from corki_gateway.store import Store
from corki.storage import SQLiteSessionRepository
from corki.protocol.items import UserMessageItem,AssistantMessageItem,new_step_id
home=Path(sys.argv[1]);s=Store(home);name='Session UI fixture '+str(__import__('time').time_ns())
i=s.save_connection({'channel':'telegram','name':name,'values':{'token':'fixture-token','session_validation':'true'}})
async def seed():
 repo=SQLiteSessionRepository(home/'sessions/corki.db')
 threads={}
 for who in ['Alice','Bob']:
  message={'id':who,'sender':who,'name':who,'conversation':who,'text':who}
  s.allows_message(s.connection(i),message)
  with s.db() as db: db.execute("UPDATE access_requests SET status='approved' WHERE channel=? AND sender_id=?",(i,who))
  message['id']+='-allowed'
  thread=s.admit(i,message,str((home/'main/default').resolve()))
  await repo.create_thread(thread,home)
  await repo.append_items(thread,(UserMessageItem(who+' question','turn-'+who),AssistantMessageItem(who+' answer','turn-'+who,new_step_id())))
  threads[who]=thread
 print(json.dumps({'id':i,**threads}))
asyncio.run(seed())
`,dirname(config.path)]);
const fixture=JSON.parse(seed.stdout);
try {
 await browser('open',base+'/#channels/'+fixture.id);
 await wait(`document.querySelectorAll('[data-open-chat]').length===2`);
 assert.equal(await evaluate(`!!document.querySelector('[data-channel-tab="history"]')`),'false');
 assert.equal(await evaluate(`!!document.querySelector('.channel-session-pane')`),'false');
 await click('[data-open-chat="Alice"]');
 await wait(`document.querySelector('.channel-chat-messages').textContent.includes('Alice answer')`);
 assert.equal(await evaluate(`document.querySelector('.channel-chat-messages').textContent.includes('Bob answer')`),'false');
 await click('[data-chat-back]');
 await click('[data-open-chat="Bob"]');
 await wait(`document.querySelector('.channel-chat-messages').textContent.includes('Bob answer')`);
 assert.equal(await evaluate(`document.querySelector('.channel-chat-messages').textContent.includes('Alice answer')`),'false');
 await click('[data-reset-current]');await click('.channel-dialog [type="submit"]');
 await wait(`!document.querySelector('.channel-dialog') && !document.querySelector('.channel-history-error').textContent && !document.querySelector('.channel-chat-messages').textContent.includes('Bob answer')`);
 await click('[data-chat-back]');await click('[data-open-chat="Alice"]');
 await wait(`document.querySelector('.channel-chat-messages').textContent.includes('Alice answer')`);
 assert.equal((await(await fetch(base+'/api/config')).json()).revision,config.revision);
 console.log('PASS session management entry, single-user chat, return, isolation and reset');
} finally {await browser('close').catch(()=>{});}
