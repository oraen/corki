// Run against a disposable manager home with two providers and a CDP Chrome.
import assert from 'node:assert/strict';
import fs from 'node:fs';

const base=process.env.MANAGER_TEST_URL||'http://127.0.0.1:17006';
const port=process.env.CHROME_DEBUG_PORT||'9225';
const target=await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`,{method:'PUT'})).json();
const socket=new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve,reject)=>{socket.onopen=resolve;socket.onerror=reject;});
let serial=0;const pending=new Map();
socket.onmessage=event=>{const message=JSON.parse(event.data);const item=pending.get(message.id);if(!item)return;pending.delete(message.id);message.error?item.reject(new Error(message.error.message)):item.resolve(message.result);};
function cdp(method,params={}){return new Promise((resolve,reject)=>{const id=++serial;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));});}
async function evaluate(expression){const result=await cdp('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(result.exceptionDetails)throw new Error(result.exceptionDetails.text);return result.result.value;}
async function wait(expression){for(let i=0;i<70;i++){if(await evaluate(expression))return;await new Promise(resolve=>setTimeout(resolve,100));}throw new Error(`Timed out: ${expression}`);}
async function click(selector){await evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);}
async function send(content){await evaluate(`document.querySelector('#simple-chat-input').value=${JSON.stringify(content)};document.querySelector('#simple-chat-send').click()`);await wait(`window.__chatRequests.length>0 && !document.querySelector('#simple-chat-send').disabled`);}
try{
 await cdp('Page.enable');await cdp('Runtime.enable');
 await cdp('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
 const markdownReply='# Markdown 标题\n\n- 第一项\n- 第二项\n\n`inline`\n\n```js\nconst answer = 42;\n```\n\n| 名称 | 值 |\n| --- | --- |\n| Corki | 42 |\n\n[安全链接](https://example.com)\n\n<script>window.__chatXss=true</script>\n\n[危险链接](javascript:alert(1))';
 await cdp('Page.addScriptToEvaluateOnNewDocument',{source:`window.__chatRequests=[];const markdownReply=${JSON.stringify(markdownReply)};const originalFetch=window.fetch.bind(window);window.fetch=async (...args)=>{if(args[0]!=='/api/model-test')return originalFetch(...args);const body=JSON.parse(args[1].body);window.__chatRequests.push(body);const reply=body.messages.at(-1).content==='MD测试'?markdownReply:'回复'+window.__chatRequests.length;return new Response(new ReadableStream({start(controller){controller.enqueue(new TextEncoder().encode('event: delta\\ndata: '+JSON.stringify({text:reply})+'\\n\\nevent: done\\ndata: {}\\n\\n'));controller.close();}}),{headers:{'Content-Type':'text/event-stream'}});};`});
 await cdp('Page.addScriptToEvaluateOnNewDocument',{source:`(()=>{const previousFetch=window.fetch;window.fetch=async (...args)=>{if(args[0]!=='/api/model-test'||JSON.parse(args[1].body).messages.at(-1).content!=='切页测试')return previousFetch(...args);window.__chatRequests.push(JSON.parse(args[1].body));return new Response(new ReadableStream({start(controller){controller.enqueue(new TextEncoder().encode('event: delta\\ndata: '+JSON.stringify({text:'前半段'})+'\\n\\n'));args[1].signal.addEventListener('abort',()=>window.__chatAborted=true);window.__finishChat=()=>{controller.enqueue(new TextEncoder().encode('event: delta\\ndata: '+JSON.stringify({text:'后半段'})+'\\n\\nevent: done\\ndata: {}\\n\\n'));controller.close();};}}),{headers:{'Content-Type':'text/event-stream'}});};})();`});
 await cdp('Page.navigate',{url:base+'/#chat'});
 await wait(`document.querySelector('#simple-chat-send')`);
 await evaluate(`localStorage.removeItem('corki.simple-chat.v1')`);
 await cdp('Page.reload',{ignoreCache:true});
 await wait(`document.querySelector('#simple-chat-send') && document.querySelector('#simple-chat-model-label').textContent==='model-a'`);
 assert.equal(await evaluate(`document.querySelector('#nav a[href="#chat"]')?.textContent.trim()`),'简单对话');
 assert.equal(await evaluate(`document.querySelector('#simple-chat-provider-label').textContent`),'fixture');
 assert(await evaluate(`(()=>{const main=document.querySelector('main').getBoundingClientRect(),panel=document.querySelector('.simple-chat-panel').getBoundingClientRect();return Math.abs(main.left-panel.left)<1 && Math.abs(main.right-panel.right)<1 && Math.abs(panel.bottom-innerHeight)<1})()`),'chat should fill the main area');
 await send('第一条');
 assert.deepEqual((await evaluate(`window.__chatRequests[0]`)).messages,[{role:'user',content:'第一条'}]);
 await click('#simple-chat-clear');assert.equal(await evaluate(`document.querySelectorAll('.simple-chat-message').length`),0);
 await send('第二条');
 assert.deepEqual((await evaluate(`window.__chatRequests[1]`)).messages,[{role:'user',content:'第一条'},{role:'assistant',content:'回复1'},{role:'user',content:'第二条'}]);
 await click('#simple-chat-new');assert.equal(await evaluate(`document.querySelectorAll('.simple-chat-message').length`),0);
 await send('第三条');assert.deepEqual((await evaluate(`window.__chatRequests[2]`)).messages,[{role:'user',content:'第三条'}]);
 await click('#simple-chat-provider-trigger');assert.equal(await evaluate(`document.querySelectorAll('#simple-chat-provider-picker [data-provider]').length`),2);
 await click('#simple-chat-model-trigger');assert.equal(await evaluate(`document.querySelectorAll('#simple-chat-model-picker [data-model]').length`),2);
 await click('#simple-chat-model-picker [data-model="model-b"]');assert.equal(await evaluate(`document.querySelector('#simple-chat-model-label').textContent`),'model-b');
 await click('#simple-chat-provider-trigger');await click('#simple-chat-provider-picker [data-provider="other"]');
 assert.equal(await evaluate(`document.querySelector('#simple-chat-model-label').textContent`),'model-c');
 await click('#simple-chat-model-trigger');assert.deepEqual(await evaluate(`Array.from(document.querySelectorAll('#simple-chat-model-picker [data-model]'),item=>item.dataset.model)`),['model-c']);
 await click('#simple-chat-model-picker [data-model="model-c"]');
 await send('第四条');const switched=await evaluate(`window.__chatRequests[3]`);assert.equal(switched.provider,'other');assert.equal(switched.model,'model-c');assert.equal(switched.messages.length,3);
 await evaluate(`window.__beforeChatReload=true`);await cdp('Page.reload',{ignoreCache:true});await wait(`!window.__beforeChatReload && document.querySelector('#simple-chat-model-label')?.textContent==='model-c' && document.querySelectorAll('.simple-chat-message').length===4`);
 assert.equal(await evaluate(`JSON.parse(localStorage.getItem('corki.simple-chat.v1')).context.length`),4);
 assert.equal(await evaluate(`document.querySelectorAll('.simple-chat-message .simple-chat-avatar').length`),4);
 assert(await evaluate(`(()=>{const left=document.querySelector('.simple-chat-message.assistant'),right=document.querySelector('.simple-chat-message.user');return left.querySelector('.simple-chat-avatar').getBoundingClientRect().left<left.querySelector('.simple-chat-bubble').getBoundingClientRect().left&&right.querySelector('.simple-chat-avatar').getBoundingClientRect().left>right.querySelector('.simple-chat-bubble').getBoundingClientRect().left})()`),'avatars should sit on opposite sides');
 assert.equal(await evaluate(`document.querySelector('.simple-chat-message.assistant .simple-chat-avatar img')?.getAttribute('src')`),'/corki-icon-ears.png');
 await send('MD测试');
 assert(await evaluate(`!!document.querySelector('.simple-chat-message:last-child .simple-chat-markdown h1')`),'Markdown heading should render');
 assert(await evaluate(`!!document.querySelector('.simple-chat-message:last-child .simple-chat-markdown li')`),'Markdown list should render');
 assert(await evaluate(`!!document.querySelector('.simple-chat-message:last-child .simple-chat-markdown pre code')`),'fenced code should render');
 assert(await evaluate(`!!document.querySelector('.simple-chat-message:last-child .simple-chat-markdown table')`),'Markdown table should render');
 assert.equal(await evaluate(`document.querySelector('.simple-chat-message:last-child .simple-chat-markdown a').rel`),'noopener noreferrer');
 assert.equal(await evaluate(`document.querySelector('.simple-chat-message:last-child .simple-chat-markdown a[href^="javascript:"]')`),null);
 assert.equal(await evaluate(`document.querySelector('.simple-chat-message:last-child script')`),null);
 assert.equal(await evaluate(`window.__chatXss||false`),false);
 assert(await evaluate(`document.querySelector('.simple-chat-message.assistant').getBoundingClientRect().width>1000`),'reading area should use more desktop width');
 await evaluate(`Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.__copied=text}}})`);
 await click('.simple-chat-message:last-child [data-chat-copy]');await wait(`window.__copied!==undefined`);
 assert.equal(await evaluate(`window.__copied`),markdownReply,'copy should use the raw model reply');
 await evaluate(`document.querySelector('#simple-chat-input').value='切页测试';document.querySelector('#simple-chat-send').click()`);
 await wait(`!!window.__finishChat && document.querySelector('.simple-chat-message:last-child')?.textContent.includes('前半段')`);
 await evaluate(`location.hash='#overview'`);await wait(`!document.querySelector('#simple-chat-messages')`);
 assert.equal(await evaluate(`window.__chatAborted||false`),false,'route change should not cancel the request');
 await evaluate(`location.hash='#chat'`);await wait(`document.querySelector('#simple-chat-state')?.textContent==='正在回复'`);
 assert(await evaluate(`document.querySelector('.simple-chat-message:last-child')?.textContent.includes('前半段')`),'partial reply should survive route change');
 await evaluate(`location.hash='#overview'`);await wait(`!document.querySelector('#simple-chat-messages')`);
 await evaluate(`window.__finishChat()`);
 await wait(`JSON.parse(localStorage.getItem('corki.simple-chat.v1')).context.at(-1)?.content==='前半段后半段'`);
 await evaluate(`location.hash='#chat'`);await wait(`document.querySelector('.simple-chat-message:last-child')?.textContent.includes('前半段后半段')`);
 assert.equal(await evaluate(`document.querySelector('.simple-chat-message:last-child').classList.contains('error')`),false);
 if(process.env.CHAT_SCREENSHOT){await cdp('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});const shot=await cdp('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(process.env.CHAT_SCREENSHOT,Buffer.from(shot.data,'base64'));}
 console.log('PASS: wide layout, raw reply copy, streaming across routes, Markdown, model picker, session actions, local persistence');
}finally{socket.close();await fetch(`http://127.0.0.1:${port}/json/close/${target.id}`);}
