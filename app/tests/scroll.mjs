import { browserLaunchOptions } from './platform.mjs';
import { chromium } from "playwright";
import http from "node:http";
import { readFile, mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import assert from "node:assert/strict";
const root = path.resolve("dist");
const server = http.createServer(async (req, res) => {
  try {
    const p = path.resolve(
      root,
      "." + new URL(req.url, "http://localhost").pathname,
    );
    if (!p.startsWith(root + path.sep)) throw Error();
    const body = await readFile(p);
    res.setHeader(
      "Content-Type",
      p.endsWith(".html")
        ? "text/html"
        : p.endsWith(".js")
          ? "text/javascript"
          : p.endsWith(".css")
            ? "text/css"
            : p.endsWith(".png")
              ? "image/png"
              : "image/jpeg",
    );
    res.end(body);
  } catch {
    res.writeHead(404).end();
  }
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const browser = await chromium.launch(browserLaunchOptions);
try {
 const page=await browser.newPage({viewport:{width:1440,height:900}});
 await page.addInitScript(()=>{
  const rows=(start,end)=>Array.from({length:end-start},(_,i)=>({id:'m'+(start+i),role:i%2?'assistant':'user',content:'消息 '+(start+i)+'\n\n'+('用于长历史阅读与滚动定位的测试内容。'.repeat(12))}));
  const saved=new Map([['ui',{selected:'thread'}], ['draft:thread', {content:'恢复的长草稿\n'.repeat(20),attachments:[],directory:'',provider:'fixture',model:'fixture',reasoning:null}]]);
  let initialConfiguration=true;
  window.corki={
   request:async(method,p)=>{
    if(method==='initialize')return{instance:'fixture',sequence:0,threadManagementVersion:2};
    if(method==='state.read')return saved.get(p.key)||null;
    if(method==='state.write'){saved.set(p.key,p.value);return{}};
    if(method==='config.read'){
     window.configurationReads=(window.configurationReads||0)+1;
     if(initialConfiguration){initialConfiguration=false;window.configurationPending=true;await new Promise(resolve=>window.releaseConfiguration=resolve);}
     return{valid:true,default:{provider:'fixture',model:'fixture'},providers:[{id:'fixture',models:[{id:'fixture',reasoningLevels:[],reasoning:null,images:false}]}]};
    }
    if(method==='threads.list')return{threads:[{id:'thread',title:'长历史测试'},{id:'short',title:'短会话测试'}],next:null};
    if(method==='threads.read'){
     if(p.threadId==='short')return{threadId:'short',instance:'fixture',sequence:window.latestSequence||0,messages:[{id:'short-user',role:'user',content:'只有一条短消息'}],next:null,turns:[],live:[],pending:[],running:false,directory:'/tmp/fixture'};
     window.historyReads=(window.historyReads||0)+1;
     if(p.before){window.olderRequested=true;await new Promise(resolve=>window.releaseOlder=resolve);}
     return{threadId:'thread',instance:'fixture',sequence:window.latestSequence||0,messages:p.before?rows(0,30):[...rows(30,90),...(window.finished?[{id:'stream',role:'assistant',content:'已完成回复'}]:[])],next:p.before?null:30,turns:[],live:[],pending:[],running:!window.finished,directory:'/tmp/fixture'};
    }
   },subscribe:callback=>{window.emit=event=>{window.latestSequence=event.sequence;callback(event)};return()=>{}},copy:async()=>{},openLink:async()=>{}
  };
 });
 await page.goto(`http://127.0.0.1:${server.address().port}/index.html`);
 await page.waitForFunction(()=>window.configurationPending);
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 await page.evaluate(()=>window.releaseConfiguration());
 await page.locator('.message').nth(59).waitFor();
 assert.equal(await page.getByRole('textbox',{name:'消息',exact:true}).inputValue(),'恢复的长草稿\n'.repeat(20));
 assert.equal(await page.getByRole('textbox',{name:'消息',exact:true}).evaluate(n=>n.style.height),'180px','Restored multiline draft must resize after startup mounts the composer');
 const metrics=()=>page.locator('.conversation-scroll').evaluate(n=>({top:n.scrollTop,gap:n.scrollHeight-n.scrollTop-n.clientHeight}));
 await page.waitForFunction(()=>{const n=document.querySelector('.conversation-scroll');return n.scrollHeight-n.scrollTop-n.clientHeight<3});
 assert.equal(await page.getByText('没有更早的记录啦',{exact:true}).count(),0,'Do not announce exhausted history before scrolling up');
 await page.locator('.conversation-scroll').evaluate(n=>{n.scrollTop=200});
 await page.getByRole('button',{name:'回到最新'}).waitFor();
 const before=(await metrics()).top;
 await page.evaluate(()=>window.emit({type:'AssistantTextDelta',threadId:'thread',instance:'fixture',sequence:1,data:{turn_id:'turn',item_id:'stream',delta:'新回复\n'.repeat(300)}}));
 await page.locator('.message').nth(60).waitFor();
 assert(Math.abs((await metrics()).top-before)<3,'Streaming must not pull an upward reader down');
 await page.getByRole('button',{name:'回到最新'}).click();
 await page.waitForFunction(()=>{const n=document.querySelector('.conversation-scroll');return n.scrollHeight-n.scrollTop-n.clientHeight<3});
 await page.locator('.conversation-scroll').evaluate(n=>{n.scrollTop=0});
 await page.waitForFunction(()=>window.olderRequested);
 await page.getByText('加载更早的消息中',{exact:true}).waitFor();
 assert.equal(await page.locator('.history-loading svg').count(),1);
 await page.locator('.conversation-scroll').evaluate(n=>{
   for(let i=0;i<10;i++) n.dispatchEvent(new WheelEvent('wheel',{deltaY:-50,bubbles:true}));
 });
 assert.equal(await page.evaluate(()=>window.historyReads),2,'Repeated top scrolling must not duplicate requests');
 // Read farther down while the older page is in flight, and receive live output.
 await page.locator('.conversation-scroll').evaluate(n=>{n.scrollTop=450});
 const anchor=page.locator('.message').nth(2);
 const anchorText=(await anchor.innerText()).match(/消息 \d+/)[0];
 const anchorTop=(await anchor.boundingBox()).y;
 await page.evaluate(()=>{
  window.emit({type:'AssistantTextDelta',threadId:'thread',instance:'fixture',sequence:2,data:{turn_id:'turn',item_id:'stream',delta:'更多回复\n'.repeat(100)}});
 });
 await page.locator('.markdown').filter({hasText:'更多回复'}).waitFor();
 await page.evaluate(()=>window.releaseOlder());
 await page.locator('.message').nth(90).waitFor();
 const restored=page.locator('.message').filter({hasText:anchorText}).first();
 assert(Math.abs((await restored.boundingBox()).y-anchorTop)<4,'Older history must preserve the current reading anchor, including scroll during loading');
 // Exercise sustained growth while the user edits the next prompt. Each chunk
 // arrives in a separate animation frame, not a single React-batched burst.
 await page.evaluate(()=>{
  window.longStreamDone=false;
  let count=0;
  const next=()=>{
   window.emit({type:'AssistantTextDelta',threadId:'thread',instance:'fixture',sequence:3+count,data:{turn_id:'turn',item_id:'stream',delta:'持续输出与输入响应。'.repeat(100)+'\n\n'}});
   if(++count<120)requestAnimationFrame(next);else window.longStreamDone=true;
  };requestAnimationFrame(next);
 });
 const prompt=page.getByRole('textbox',{name:'消息',exact:true});
 await prompt.fill('流式期间保留下一条输入');
 await page.waitForFunction(()=>window.longStreamDone,{},{timeout:20000});
 assert.equal(await prompt.inputValue(),'流式期间保留下一条输入');
 await page.locator('.message').last().filter({hasText:'持续输出与输入响应。'}).waitFor();
 assert(Math.abs((await restored.boundingBox()).y-anchorTop)<4,'Long output must not move the reader anchor');
 assert.equal(await page.evaluate(()=>window.historyReads),2,'120 streamed chunks must not read history');
 const beforeWarning=await page.locator('.conversation-scroll').boundingBox();
 await page.evaluate(()=>window.emit({type:'model.health',threadId:'thread',instance:'fixture',sequence:123,data:{warning:true}}));
 await page.getByText('当前模型响应较慢，请耐心等待',{exact:true}).waitFor();
 assert.deepEqual(await page.locator('.conversation-scroll').boundingBox(),beforeWarning,'Model warning overlay cannot shift the conversation');
 assert.equal(await page.locator('.model-health-banner').evaluate(n=>getComputedStyle(n).position),'absolute');
 await page.screenshot({path:path.join(os.tmpdir(),'corki-model-health-warning.png')});
 await page.evaluate(()=>window.emit({type:'model.health',threadId:'thread',instance:'fixture',sequence:124,data:{warning:false}}));
 await page.locator('.model-health-banner').waitFor({state:'detached'});
 await page.evaluate(()=>{window.finished=true;window.emit({type:'run.settled',threadId:'thread',instance:'fixture',sequence:125,data:{requestId:'r'}})});
 await page.locator('.markdown').filter({hasText:'已完成回复'}).waitFor();
 assert.equal(await page.locator('.message').count(),91,'Settled refresh must retain all loaded older pages');
 assert.equal(await page.evaluate(()=>window.historyReads),3,'Settlement needs only one authoritative refresh');
 assert.equal(await page.getByText('没有更早的记录啦',{exact:true}).count(),1,'Already exhausted history must remain exhausted');
 await page.screenshot({path:path.join(os.tmpdir(),'corki-desktop-long-history.png')});
 await page.getByText('短会话测试',{exact:true}).click();
 await page.getByText('只有一条短消息',{exact:true}).waitFor();
 assert.equal(await page.evaluate(()=>window.configurationReads),1,'Switching existing conversations must reuse the loaded catalog');
 await page.waitForFunction(()=>{const n=document.querySelector('.conversation-scroll');return n.scrollHeight<=n.clientHeight+2});
 await page.locator('.conversation-scroll').evaluate(n=>n.dispatchEvent(new WheelEvent('wheel',{deltaY:-100,bubbles:true})));
 assert.equal(await page.locator('.history-loading').count(),0,'Short non-scrollable conversations must not show history prompts');
 await page.locator('.new-chat').click();
 await page.waitForFunction(()=>window.configurationReads===2);
 assert.equal(await page.locator('.history-loading').count(),0,'New chat must not show history prompts');
 console.log('PASS long-history initial bottom, upward reading during stream, return-to-latest and pagination anchor, plus 120 sustained large chunks while editing');
} finally {await browser.close();server.close();}
