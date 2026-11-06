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
const click=async selector=>{await wait(`!!document.querySelector(${JSON.stringify(selector)})`);await evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center'})`);await browser('click',selector);};
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
 await browser('open',base+'/#channels');
 await wait(`!!document.querySelector('[data-config="${fixture.id}"]')`);
 assert.equal(await evaluate(`document.querySelector('[data-config="${fixture.id}"]').textContent.startsWith('Session UI fixture ')`),'true');
 assert.equal(await evaluate(`!!document.querySelector('.actions [data-config]')`),'false');
 if(process.env.MANAGER_TEST_SCREENSHOTS) await browser('screenshot',process.env.MANAGER_TEST_SCREENSHOTS+'/connections.png');
 await click('[data-config="'+fixture.id+'"]');
 await wait(`document.querySelectorAll('[data-open-chat]').length===6`);
 assert.equal(await evaluate(`document.querySelector('.channel-application-table').textContent.includes('备注')`),'false');
 assert.equal(await evaluate(`document.querySelector('.channel-application-table').textContent.includes('接入消息')`),'true');
 assert.equal(await evaluate(`document.querySelector('.channel-application-table').textContent.includes('上次对话')`),'true');
 assert.equal(await evaluate(`document.querySelector('[name="application_sort"]').value==='id'`),'true');
 for(const [selector,hint] of [['.channel-application-status','状态筛选'],['.channel-application-sort','排序方式']]){
  await click(selector+' summary');
  await wait(`document.querySelector('${selector} [popover]').matches(':popover-open')`);
  assert.equal(await evaluate(`document.querySelector('${selector} .channel-select-hint').textContent===${JSON.stringify(hint)}`),'true');
  assert.equal(await evaluate(`(()=>{const box=document.querySelector('${selector} summary').getBoundingClientRect(), menu=document.querySelector('${selector} [popover]').getBoundingClientRect();return Math.min(Math.abs(menu.top-box.bottom),Math.abs(menu.bottom-box.top))<=1})()`),'true');
  if(process.env.MANAGER_TEST_SCREENSHOTS && selector.includes('sort'))await browser('screenshot',process.env.MANAGER_TEST_SCREENSHOTS+'/sort-menu.png');
  await browser('press','Escape');
 }
 await click('.channel-application-sort summary');
 await click('.channel-application-sort [data-option="tokens"]');
 await wait(`document.querySelector('[name="application_sort"]').value==='tokens' && document.querySelectorAll('[data-open-chat]').length===6`);
 await click('.channel-application-sort summary');
 await click('.channel-application-sort [data-option="id"]');
 await wait(`document.querySelector('[name="application_sort"]').value==='id' && document.querySelectorAll('[data-open-chat]').length===6`);

 assert.equal(await evaluate(`!!document.querySelector('[data-channel-tab="history"]')`),'false');
 assert.equal(await evaluate(`document.querySelectorAll('[data-request-actions] [data-open-chat]').length`),'0');
 assert.equal(await evaluate(`!!document.querySelector('.channel-session-pane')`),'false');
 if(process.env.MANAGER_TEST_SCREENSHOTS) await browser('screenshot',process.env.MANAGER_TEST_SCREENSHOTS+'/users.png');
 await click('.channel-request-id[data-open-chat="Alice"]');
 await wait(`document.querySelector('.channel-chat-messages')?.textContent.includes('Alice answer')`);
 assert.equal(await evaluate(`document.querySelector('#crumb').textContent.startsWith('会话页')`),'true');
 assert.equal(await evaluate(`!!document.querySelector('[data-reset-current], [data-chat-back], [data-chat-name]')`),'false');
 assert.equal(await evaluate(`document.querySelector('.channel-detail-toolbar').hidden`),'true');
 assert.equal(await evaluate(`getComputedStyle(document.querySelector('.channel-detail-toolbar')).display==='none'`),'true');
 assert.equal(await evaluate(`Math.round(document.querySelector('.channel-chat-panel').getBoundingClientRect().top)`),'92');
 assert.equal(await evaluate(`Math.abs(document.querySelector('.channel-transcript').getBoundingClientRect().bottom-document.querySelector('.channel-chat-panel').getBoundingClientRect().bottom)<=2`),'true');
 if(process.env.MANAGER_TEST_SCREENSHOTS) await browser('screenshot',process.env.MANAGER_TEST_SCREENSHOTS+'/chat.png');
 await browser('reload');
 await wait(`document.querySelector('.channel-chat-messages')?.textContent.includes('Alice answer')`);
 assert.equal(await evaluate(`document.querySelector('.channel-chat-messages')?.textContent.includes('Bob answer')`),'false');
 await click('.header-back');
 await click('.channel-request-name[data-open-chat="Bob"]');
 await wait(`document.querySelector('.channel-chat-messages')?.textContent.includes('Bob answer')`);
 assert.equal(await evaluate(`document.querySelector('.channel-chat-messages')?.textContent.includes('Alice answer')`),'false');
 await click('.header-back');
 await wait(`!!document.querySelector('[data-reset-chat="Bob"]')`);
 await click('[data-reset-chat="Bob"]');await click('.channel-dialog [type="submit"]');
 await wait(`!document.querySelector('.channel-dialog')`);
 await click('.channel-request-name[data-open-chat="Bob"]');
 await wait(`!!document.querySelector('.channel-chat-empty') && !document.querySelector('.channel-history-error').textContent && !document.querySelector('.channel-chat-messages')?.textContent.includes('Bob answer')`);
 await click('.header-back');await click('.channel-request-id[data-open-chat="Alice"]');
 await wait(`document.querySelector('.channel-chat-messages')?.textContent.includes('Alice answer')`);
 assert.equal((await(await fetch(base+'/api/config')).json()).revision,config.revision);
 console.log('PASS session management entry, single-user chat, return, isolation and reset');
} finally {await browser('close').catch(()=>{});}
