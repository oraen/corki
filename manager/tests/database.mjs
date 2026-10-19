// Run only against a disposable Manager home containing the example.db fixture.
import assert from 'node:assert/strict';
import fs from 'node:fs';

const base=process.env.MANAGER_TEST_URL||'http://127.0.0.1:17018';
const port=process.env.CHROME_DEBUG_PORT||'9447';
const target=await(await fetch(`http://127.0.0.1:${port}/json/new?about:blank`,{method:'PUT'})).json();
const socket=new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve,reject)=>{socket.onopen=resolve;socket.onerror=reject;});
let serial=0;const pending=new Map();
socket.onmessage=event=>{const message=JSON.parse(event.data),item=pending.get(message.id);if(!item)return;pending.delete(message.id);message.error?item.reject(new Error(message.error.message)):item.resolve(message.result);};
function cdp(method,params={}){return new Promise((resolve,reject)=>{const id=++serial;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));});}
async function evaluate(expression){const result=await cdp('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(result.exceptionDetails)throw new Error(result.exceptionDetails.text);return result.result.value;}
async function wait(expression){for(let i=0;i<70;i++){if(await evaluate(`Boolean(${expression})`))return;await new Promise(resolve=>setTimeout(resolve,100));}throw new Error(`Timed out: ${expression}`);}
async function editJSON(value){
 await evaluate(`document.querySelector('.dms-editor .cm-content').focus()`);
 await cdp('Input.dispatchKeyEvent',{type:'keyDown',key:'a',code:'KeyA',modifiers:4,commands:['selectAll']});
 await cdp('Input.dispatchKeyEvent',{type:'keyUp',key:'a',code:'KeyA',modifiers:4});
 await cdp('Input.insertText',{text:JSON.stringify(value,null,2)});
}
try {
 await cdp('Page.enable');await cdp('Runtime.enable');
 await cdp('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
 await cdp('Page.navigate',{url:base+'/#database'});
 await wait(`document.querySelector('[data-table="notes"]')`);
 await evaluate(`document.querySelector('[data-table="notes"]').click()`);
 await wait(`document.querySelector('.dms-grid')?.textContent.includes('使用指引')`);
 await evaluate(`document.querySelector('[data-do="insert"]').click()`);
 await wait(`document.querySelector('.dms-editor .cm-content')`);
 await editJSON({title:'新增测试',content:'Markdown **内容**'});
 await evaluate(`document.querySelector('[data-save]').click()`);
 await wait(`!document.querySelector('.dms-dialog') && document.querySelector('.dms-grid')?.textContent.includes('新增测试')`);
 await evaluate(`document.querySelector('[data-do="edit"][data-row="1"]').click()`);
 await editJSON({id:2,title:'编辑测试',content:'更新成功'});
 await evaluate(`document.querySelector('[data-save]').click()`);
 await wait(`!document.querySelector('.dms-dialog') && document.querySelector('.dms-grid')?.textContent.includes('编辑测试')`);
 await evaluate(`document.querySelector('.dms-search input').value='编辑测试';document.querySelector('.dms-search').requestSubmit()`);
 await wait(`document.querySelector('.dms-footer')?.textContent.includes('共 1 条')`);
 await evaluate(`document.querySelector('[data-do="delete"]').click();document.querySelector('[data-save]').click()`);
 await wait(`!document.querySelector('.dms-dialog') && document.querySelector('.dms-footer')?.textContent.includes('共 0 条')`);
 await evaluate(`document.querySelector('.dms-search input').value='';document.querySelector('.dms-search').requestSubmit()`);
 await wait(`document.querySelector('.dms-grid')?.textContent.includes('使用指引')`);
 fs.writeFileSync('/tmp/corki-dms-sqlite.png',Buffer.from((await cdp('Page.captureScreenshot',{format:'png'})).data,'base64'));
 await evaluate(`document.querySelector('[data-tab="schema"]').click()`);
 assert(await evaluate(`document.querySelector('.dms-ddl').textContent.includes('CREATE TABLE')`));
 await evaluate(`document.querySelector('[data-do="sql"]').click()`);
 await wait(`document.querySelector('.sql-page-editor .cm-content')`);
 assert.equal(await evaluate(`document.querySelectorAll('dialog[open]').length`),0);
 assert(await evaluate(`location.hash.startsWith('#database/sql/') && document.querySelector('.header-back').getAttribute('href')==='#database'`));
 async function setSQL(sql){
  await evaluate(`document.querySelector('.sql-page-editor .cm-content').focus()`);
  await cdp('Input.dispatchKeyEvent',{type:'keyDown',key:'a',code:'KeyA',modifiers:4,commands:['selectAll']});
  await cdp('Input.dispatchKeyEvent',{type:'keyUp',key:'a',code:'KeyA',modifiers:4});
  await cdp('Input.insertText',{text:sql});
 }
 await setSQL("SELECT 'first' AS value; SELECT 'second' AS value;");
 await evaluate(`document.querySelector('[data-sql-run]').click()`);
 await wait(`document.querySelectorAll('.sql-statement-result').length===2`);
 assert(await evaluate(`document.querySelector('.sql-result-body').textContent.includes('second')`));
 await evaluate(`document.querySelector('[data-sql-tab]').dispatchEvent(new MouseEvent('dblclick',{bubbles:true}))`);
 await wait(`document.querySelector('.sql-tab-name')`);
 await evaluate(`document.querySelector('.sql-tab-name').value='持久化查询';document.querySelector('.sql-tab-name').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))`);
 await wait(`document.querySelector('[data-sql-status]')?.textContent==='已保存'`);
 await evaluate('window.__sqlReloadSentinel=true');
 await cdp('Page.reload');
 await wait(`!window.__sqlReloadSentinel && document.querySelector('[data-sql-tab]')?.textContent==='持久化查询' && document.querySelectorAll('.sql-statement-result').length===2`);
 assert(await evaluate(`document.querySelector('.sql-page-editor').textContent.includes('second')`));
 await setSQL("SELECT 'do-not-execute' AS value;\nSELECT 'selected-only' AS value;");
 await cdp('Input.dispatchKeyEvent',{type:'keyDown',key:'Home',code:'Home',modifiers:8});
 await cdp('Input.dispatchKeyEvent',{type:'keyUp',key:'Home',code:'Home',modifiers:8});
 assert.equal(await evaluate(`window.getSelection().toString()`),"SELECT 'selected-only' AS value;");
 await evaluate(`document.querySelector('[data-sql-run]').click()`);
 await wait(`document.querySelectorAll('[data-sql-result]').length===2 && document.querySelector('.sql-result-body')?.textContent.includes('selected-only')`);
 assert.equal(await evaluate(`document.querySelectorAll('.sql-statement-result').length`),1);
 await setSQL("UPDATE notes SET content='batch-changed'; SELECT missing FROM notes;");
 await evaluate(`document.querySelector('[data-sql-run]').click()`);
 await wait(`document.querySelector('.dms-sql-confirm')?.hidden===false`);
 await evaluate(`document.querySelector('[data-sql-cancel]').click()`);
 assert.equal(await evaluate(`document.querySelectorAll('[data-sql-result]').length`),2);
 await evaluate(`document.querySelector('[data-sql-run]').click()`);
 await wait(`document.querySelector('.dms-sql-confirm')?.hidden===false`);
 await evaluate(`document.querySelector('[data-sql-confirm]').click()`);
 await wait(`document.querySelector('.sql-result-body .dms-error')?.textContent.includes('回滚')`);
 assert.equal(await evaluate(`fetch('/api/databases?database=example.db&table=notes').then(r=>r.json()).then(d=>d.rows[0].values.content)`),'这是一条用于验证数据库管理的测试记录。');
 await setSQL('SELECT * FROM notes');
 await evaluate(`document.querySelector('[data-sql-run]').click()`);
 await wait(`document.querySelectorAll('[data-sql-result]').length===4 && document.querySelector('.sql-result-body')?.textContent.includes('使用指引')`);
 fs.writeFileSync('/tmp/corki-sql-page.png',Buffer.from((await cdp('Page.captureScreenshot',{format:'png'})).data,'base64'));
 assert(await evaluate(`document.querySelector('[data-sql-new]').checkVisibility()`));
 await evaluate(`document.querySelector('[data-sql-new]').click()`);
 await wait(`document.querySelectorAll('[data-sql-tab]').length===2`);
 await setSQL('SELECT 42 AS saved');
 await wait(`document.querySelector('[data-sql-status]')?.textContent==='已保存'`);
 await evaluate(`document.querySelectorAll('[data-sql-tab]')[0].click()`);
 await wait(`document.querySelectorAll('[data-sql-result]').length===4`);
 await evaluate(`document.querySelector('[data-sql-close-result]').click()`);
 await wait(`document.querySelectorAll('[data-sql-result]').length===3`);
 await evaluate(`document.querySelector('.header-back').click()`);
 await wait(`document.querySelector('[data-do="sql"]:not(:disabled)')`);
 await evaluate(`document.querySelector('[data-do="sql"]').click()`);
 await wait(`document.querySelectorAll('[data-sql-tab]').length===2 && document.querySelectorAll('[data-sql-result]').length===3`);
 await evaluate(`document.querySelector('[data-sql-close-query]').click()`);
 await wait(`document.querySelectorAll('[data-sql-tab]').length===1`);
 assert.equal(await evaluate(`fetch('/api/sql-workspace').then(r=>r.json()).then(d=>d.queries[0].results.length)`),0);
 await evaluate(`document.querySelector('[data-sql-close-query]').click()`);
 await wait(`document.querySelectorAll('[data-sql-tab]').length===0`);
 assert.equal(await evaluate(`fetch('/api/sql-workspace').then(r=>r.json()).then(d=>d.queries.length)`),0);
 console.log('PASS: SQLite CRUD; SQL page, batch/selection, rename, reload persistence, rollback, query/result tab deletion');
}catch(error){console.error(await evaluate('document.body.innerText'));throw error;}finally{await cdp('Page.close');socket.close();}
