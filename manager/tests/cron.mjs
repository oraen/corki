// Run only against a disposable Manager home with a local fake model provider.
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
async function wait(expression){for(let i=0;i<70;i++){if(await evaluate(expression))return;await new Promise(resolve=>setTimeout(resolve,100));}throw new Error(`Timed out: ${expression}`);}
try {
 await cdp('Page.enable');await cdp('Runtime.enable');
 await cdp('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
 await cdp('Page.navigate',{url:base+'/#cron'});
 await wait(`document.querySelector('.cron-heading')`);
 assert(await evaluate(`document.querySelector('a[href="#tools"]').nextElementSibling.getAttribute('href')==='#cron' && document.querySelector('a[href="#cron"]').nextElementSibling.getAttribute('href')==='#chat'`));
 await evaluate(`document.querySelector('[data-cron="create"]').click()`);
 await wait(`document.querySelector('#editor').open`);
 assert.equal(await evaluate(`document.querySelectorAll('#fields select').length`),0);
 assert.equal(await evaluate(`document.querySelector('[name=provider]').value`),'fixture');
 assert.equal(await evaluate(`document.querySelector('[name=model]').value`),'fixture-model');
 assert.equal(await evaluate(`document.querySelector('[name=overlap_policy]').value`),'skip');
 assert.equal(await evaluate(`document.querySelector('[name=timeout_seconds]').value`),'');
 await evaluate(`document.querySelector('[name=overlap_policy]').closest('.cron-select').querySelector('.select-trigger').click();document.querySelector('[name=overlap_policy]').closest('.cron-select').querySelector('[data-value="restart"]').click();document.querySelector('[name=timeout_seconds]').value='90'`);

 await evaluate(`document.querySelector('[name=provider]').closest('.cron-select').querySelector('.select-trigger').click();document.querySelector('[name=provider]').closest('.cron-select').querySelector('[data-value="alternate"]').click()`);
 assert.equal(await evaluate(`document.querySelector('[name=model]').value`),'alternate-first');
 await evaluate(`document.querySelector('[name=mode]').closest('.cron-select').querySelector('.select-trigger').click();document.querySelector('[name=mode]').closest('.cron-select').querySelector('[data-value="hourly"]').click()`);
 assert.equal(await evaluate(`document.querySelector('[name=mode]').value`),'hourly');
 assert.equal(await evaluate(`document.querySelector('[name=timing]').value`),'00');
 assert.equal(await evaluate(`document.querySelector('[data-value=interval]')===null`),true);
 await evaluate(`document.querySelector('[name=timing]').value='60'`);
 assert.equal(await evaluate(`document.querySelector('[name=timing]').checkValidity()`),false);
 await evaluate(`document.querySelector('[name=timing]').value='35'`);
 if(process.env.CRON_SCREENSHOT){const shot=await cdp('Page.captureScreenshot',{format:'png'});fs.writeFileSync(process.env.CRON_SCREENSHOT.replace('.png','-create.png'),Buffer.from(shot.data,'base64'));}
 await evaluate(`document.querySelector('[name=name]').value='浏览器定时测试';document.querySelector('[name=prompt]').value='请简要整理本次测试资料。';document.querySelector('[name=timing]').value='35';document.querySelector('#save').click()`);
 await wait(`!document.querySelector('#editor').open && [...document.querySelectorAll('.cron-task-row')].some(e=>e.textContent.includes('浏览器定时测试'))`);
 assert(await evaluate(`fetch('/api/cron').then(r=>r.json()).then(r=>r.jobs.some(j=>j.name==='浏览器定时测试'&&j.overlap_policy==='restart'&&j.timeout_seconds===90))`));
 const jobId=await evaluate(`[...document.querySelectorAll('.cron-task-row')].find(e=>e.textContent.includes('浏览器定时测试')).querySelector('[data-id]').dataset.id`);
 await evaluate(`document.querySelector('[data-cron="pause"][data-id="${jobId}"]').click()`);
 await wait(`document.querySelector('[data-cron="resume"][data-id="${jobId}"]')`);
 await evaluate(`document.querySelector('[data-cron="resume"][data-id="${jobId}"]').click()`);
 await wait(`document.querySelector('[data-cron="pause"][data-id="${jobId}"]')`);
 await evaluate(`document.querySelector('[data-cron="edit"][data-id="${jobId}"]').click()`);
 await wait(`document.querySelector('#editor').open`);
 assert.equal(await evaluate(`document.querySelector('[name=mode]').value`),'hourly');
 assert.equal(await evaluate(`document.querySelector('[name=timing]').value`),'35');
 assert.equal(await evaluate(`document.querySelector('[name=provider]').value`),'alternate');
 assert.equal(await evaluate(`document.querySelector('[name=model]').value`),'alternate-first');
 await evaluate(`document.querySelector('[name=prompt]').value='请生成一份简要摘要。';document.querySelector('#save').click()`);
 await wait(`!document.querySelector('#editor').open`);
 await evaluate(`document.querySelector('[data-cron="run"][data-id="${jobId}"]').click()`);
 await wait(`fetch('/api/cron').then(r=>r.json()).then(s=>s.jobs.find(j=>j.id==='${jobId}')?.last_run?.status==='completed')`);
 await evaluate(`document.querySelector('[data-cron="history"][data-id="${jobId}"]').click()`);
 await wait(`document.querySelector('[data-cron-detail]')`);
 assert.equal(await evaluate(`document.querySelector('#editor').open`),false);
 assert.equal(await evaluate(`document.querySelector('.cron-filter-selection').textContent`),'浏览器定时测试');
 assert(await evaluate(`document.querySelector('.cron-record-table').textContent.includes('浏览器定时测试')`));
 await evaluate(`document.querySelector('.cron-record-filter summary').click()`);
 await evaluate(`document.querySelector('[data-record-name="每日工作摘要"]').click()`);
 await wait(`document.querySelectorAll('[data-record-name][aria-checked=true]').length===2`);
 await evaluate(`document.querySelector('#cron-filter-search').value='每日';document.querySelector('#cron-filter-search').dispatchEvent(new Event('input'))`);
 assert.equal(await evaluate(`document.querySelectorAll('[data-record-name]').length`),1);
 await evaluate(`document.querySelector('#cron-filter-clear').click()`);
 await wait(`document.querySelector('.cron-filter-selection')?.textContent==='全部任务' && document.querySelector('[data-cron-detail]')`);
 await evaluate(`document.querySelector('[data-cron-detail]').click()`);
 await wait(`document.querySelector('#editor').open && document.querySelector('.cron-detail-title')`);
 assert(await evaluate(`document.querySelector('.cron-output').textContent.includes('定时任务已完成')`));
 if(process.env.CRON_SCREENSHOT){const shot=await cdp('Page.captureScreenshot',{format:'png'});fs.writeFileSync(process.env.CRON_SCREENSHOT.replace('.png','-detail.png'),Buffer.from(shot.data,'base64'));}
 await evaluate(`document.querySelector('#close-dialog').click()`);
 if(process.env.CRON_SCREENSHOT){const shot=await cdp('Page.captureScreenshot',{format:'png'});fs.writeFileSync(process.env.CRON_SCREENSHOT.replace('.png','-records.png'),Buffer.from(shot.data,'base64'));}
 await evaluate(`document.querySelector('[data-cron-view="tasks"]').click()`);
 assert.equal(await evaluate(`getComputedStyle(document.querySelector('.cron-task-buttons')).display`),'grid');
 assert.equal(await evaluate(`document.querySelector('[data-cron=edit]').textContent`),'编辑');
 assert.equal(await evaluate(`document.querySelector('[data-cron=delete]').textContent`),'删除');
 assert.deepEqual(await evaluate(`[...document.querySelectorAll('.cron-task-table th')].map(t=>t.textContent)`),['任务名称','执行计划','工作目录','下次运行','供应商','模型','启用','操作']);
 assert.equal(await evaluate(`document.querySelector('.cron-task-table').textContent.includes('默认模型')||document.querySelector('.cron-task-table').textContent.includes('下次执行')`),false);
 if(process.env.CRON_SCREENSHOT){const shot=await cdp('Page.captureScreenshot',{format:'png'});fs.writeFileSync(process.env.CRON_SCREENSHOT,Buffer.from(shot.data,'base64'));}
 await evaluate(`document.querySelector('[data-cron="delete"][data-id="${jobId}"]').click()`);
 await wait(`document.querySelector('#editor').open`);
 assert.equal(await evaluate(`document.querySelector('#save').textContent`),'删除');
 await evaluate(`document.querySelector('#save').click()`);
 await wait(`!document.querySelector('#editor').open && !document.querySelector('[data-id="${jobId}"]')`);
 console.log('PASS: navigation order, custom controls, create, toggle, edit, isolated real worker, result, delete');
}catch(error){console.error(await evaluate('document.body.innerText'));throw error;}finally{socket.close();await fetch(`http://127.0.0.1:${port}/json/close/${target.id}`);}
