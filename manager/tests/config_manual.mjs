// Read-only browser regression. Use a disposable manager home.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

const base=process.env.MANAGER_TEST_URL||'http://127.0.0.1:17004';
const debug=process.env.CHROME_DEBUG_PORT||'9227';
const tabs=await(await fetch(`http://127.0.0.1:${debug}/json`)).json();
const tab=tabs.find(item=>item.type==='page'&&item.url.startsWith(base));
assert(tab,'Open the disposable manager in debugging Chrome');
const fixture=await(await fetch(`${base}/api/config`)).json();
assert(fixture.path.includes('/corki-config-manual-qa/'),'Dedicated QA home required');
const setup=await fetch(`${base}/api/mutate`,{method:'POST',headers:{'Content-Type':'application/json','X-Corki-Manager':'1'},body:JSON.stringify({kind:'config',action:'save',content:fixture.content+'\n',revision:fixture.revision})});
assert(setup.ok,(await setup.text()));
const socket=new WebSocket(tab.webSocketDebuggerUrl);
await new Promise(resolve=>socket.onopen=resolve);
let id=0;
const pending=new Map(),errors=[];
socket.onmessage=event=>{
  const message=JSON.parse(event.data);
  if(message.method==='Runtime.exceptionThrown')errors.push(message.params.exceptionDetails.text);
  const callback=pending.get(message.id);
  if(!callback)return;
  pending.delete(message.id);
  message.error?callback.reject(message.error):callback.resolve(message.result);
};
const cdp=(method,params={})=>new Promise((resolve,reject)=>{
  const next=++id;
  pending.set(next,{resolve,reject});
  socket.send(JSON.stringify({id:next,method,params}));
});
const evaluate=async expression=>{
  const result=await cdp('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});
  if(result.exceptionDetails)throw Error(result.exceptionDetails.text);
  return result.result.value;
};
const click=selector=>evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);
const wait=async expression=>{
  for(let attempt=0;attempt<80;attempt++){
    if(await evaluate(`Boolean(${expression})`))return;
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  throw Error(`Timed out: ${expression}`);
};

try{
  await cdp('Runtime.enable');
  await cdp('Page.enable');
  await cdp('Emulation.setDeviceMetricsOverride',{width:1500,height:1100,deviceScaleFactor:1,mobile:false});
  await cdp('Page.navigate',{url:`${base}/#config`});
  await cdp('Page.reload',{ignoreCache:true});
  await new Promise(resolve=>setTimeout(resolve,400));
  await wait('window.CorkiConfig?.doc && CorkiConfig.editor');
  assert(await evaluate('CorkiConfig.doc.path.includes("/corki-config-manual-qa/")'));
  const before=await evaluate('CorkiConfig.doc.content');
  assert(await evaluate('document.querySelector("[data-config-action=manual]").compareDocumentPosition(document.querySelector("[data-config-action=refresh]")) & Node.DOCUMENT_POSITION_FOLLOWING'));
  await click('[data-config-action=manual]');
  await wait('CorkiConfig.tab==="manual" && document.querySelector(".manual-tree")');
  assert.equal(await evaluate('document.querySelector(".config-view-back").textContent.trim()'),'返回配置');
  assert(await evaluate('document.querySelectorAll("[data-manual-field]").length>100'));
  assert(await evaluate('Array.from(document.querySelector(".manual-tree-nodes").children).every(node=>/^[a-z_]+$/.test((node.matches("[data-manual-field]")?node:node.querySelector(":scope > [data-manual-field]")).querySelector("span:first-child").textContent.trim()))'));
  await click('[data-manual-field="default"]');
  assert(await evaluate('document.querySelector(".manual-detail").textContent.includes("是否必须")'));
  assert(await evaluate('document.querySelector(".manual-detail").textContent.includes("默认值")'));
  assert(await evaluate('JSON.parse(document.querySelector(".manual-example").textContent).default.model==="demo-model"'));
  await click('[data-manual-field="default.provider"]');
  assert(await evaluate('document.querySelector(".manual-detail").textContent.includes("配置 default 时必填")'));
  await click('[data-manual-field="providers"]');
  await click('[data-manual-field="providers.[provider]"]');
  await click('[data-manual-field="providers.[provider].api_mode"]');
  assert(await evaluate('document.querySelector(".manual-detail").textContent.includes("Chat Completions")'));
  assert(await evaluate('document.querySelector(".manual-detail").textContent.includes("Responses")'));
  assert(await evaluate('JSON.parse(document.querySelector(".manual-example").textContent).providers.demo.api_mode==="chat_completions"'));
  const image=await cdp('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
  fs.writeFileSync(join(tmpdir(),'corki-config-manual.png'),Buffer.from(image.data,'base64'));
  await click('[data-manual-field="skills"]');
  await click('[data-manual-field="skills.config"]');
  await click('[data-manual-field="skills.config.[0]"]');
  await click('[data-manual-field="skills.config.[0].path"]');
  assert(await evaluate('Array.isArray(JSON.parse(document.querySelector(".manual-example").textContent).skills.config)'));
  await click('[data-config-action=back-to-current]');
  await wait('CorkiConfig.tab==="current" && CorkiConfig.editor');
  await click('[data-config-action=edit]');
  await evaluate('CorkiConfig.editor.dispatch({changes:{from:CorkiConfig.editor.state.doc.length,insert:"\\n"}})');
  await wait('CorkiConfig.dirty');
  const draft=await evaluate('CorkiConfig.draft');
  await click('[data-config-action=manual]');
  await click('[data-config-action=back-to-current]');
  await wait('CorkiConfig.editor');
  assert.equal(await evaluate('CorkiConfig.draft'),draft);
  assert.equal(await evaluate('CorkiConfig.editor.state.doc.toString()'),draft);
  await click('[data-config-action=compare]');
  await wait('CorkiConfig.tab==="compare" && document.querySelector(".config-view-back")');
  assert.equal(await evaluate('document.querySelector(".config-view-back").textContent.trim()'),'返回配置');
  await click('[data-config-action=back-to-current]');
  await wait('CorkiConfig.tab==="current" && CorkiConfig.editor');
  assert.equal(await evaluate('CorkiConfig.draft'),draft);
  assert.equal(await evaluate('api("config").then(value=>value.content)'),before);
  assert.deepEqual(errors,[]);
  console.log('PASS: read-only guide, tree, enum details, draft preservation, no config write');
}finally{
  await cdp('Emulation.clearDeviceMetricsOverride');
  socket.close();
}
