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
 const page=await browser.newPage({viewport:{width:1280,height:800}});
 const outbound=[];page.on('request',request=>{if(request.url().includes('example.invalid'))outbound.push(request.url())});
 await page.addInitScript(()=>{
  window.payload='# 内容安全测试\n\n| 列 | 值 |\n| --- | --- |\n| 内容 | 正常 |\n\n'
    +'<script>window.injected=true</script><img src="https://example.invalid/pixel" onerror="window.injected=true">'
    +'<svg><image href="https://example.invalid/svg" /></svg><iframe src="https://example.invalid/frame"></iframe>'
    +'<math><mi>x</mi></math><a href="javascript:window.injected=true">恶意链接</a>\n\n'
    +'[正常链接](https://example.invalid/document)\n\n```javascript\nconst text = "<script>literal</script>";\n```';
  const state=new Map([['ui',{selected:'thread'}]]);
  window.links=[];window.corki={
   request:async(method,p)=>{
    if(method==='initialize')return{instance:'fixture',sequence:0,threadManagementVersion:2};
    if(method==='state.read')return state.get(p.key)||null;
    if(method==='state.write'){state.set(p.key,p.value);return{}};
    if(method==='config.read')return{valid:true,default:{provider:'fixture',model:'fixture'},providers:[{id:'fixture',models:[{id:'fixture',reasoningLevels:[],images:false}]},{id:'qwen',models:[{id:'fixture',reasoningLevels:[],images:false}]}]};
    if(method==='threads.list')return{threads:[{id:'thread',title:'内容测试',cwd:'/tmp/中文 工作目录'}],next:null,directories:[{path:'/tmp/中文 工作目录',name:'中文 工作目录'}]};
    if(method==='threads.read')return{threadId:'thread',instance:'fixture',sequence:0,messages:[{id:'question',role:'user',content:'请展示 Markdown 回答与表格。'},{id:'answer',role:'assistant',content:window.payload}],next:null,turns:[],live:[],pending:[],running:false,directory:'/tmp/fixture'};
   },subscribe:()=>()=>{},copy:async text=>window.copied=text,openLink:async url=>window.links.push(url)
  };
 });
 await page.goto(`http://127.0.0.1:${server.address().port}/index.html`);
 await page.getByRole('heading',{name:'内容安全测试'}).waitFor();
 const menu=page.locator('.thread-menu').first();
 await menu.locator('summary').click();assert(await menu.evaluate(n=>n.open));
 await page.getByRole('heading',{name:'内容安全测试'}).click();assert(!(await menu.evaluate(n=>n.open)));
 await menu.locator('summary').click();await page.keyboard.press('Escape');
 assert(!(await menu.evaluate(n=>n.open)));assert(await menu.locator('summary').evaluate(n=>n===document.activeElement));
 await menu.locator('summary').click();await page.getByRole('textbox',{name:'消息',exact:true}).focus();
 assert(!(await menu.evaluate(n=>n.open)));
 await menu.locator('summary').click();
 for(const name of ['重命名','归档']) assert.equal(await menu.getByRole('button',{name,exact:true}).evaluate(n=>getComputedStyle(n).color),'rgb(48, 51, 49)');
 await page.keyboard.press('Escape');
 const recent=page.getByRole('button',{name:'最近会话',exact:true});
 assert.equal(await recent.getAttribute('aria-expanded'),'true');
 await recent.click();
 assert.equal(await page.locator('.thread-select:visible').count(),0);
 const workspace=page.getByRole('button',{name:'中文 工作目录',exact:true});
 assert.equal(await workspace.getAttribute('title'),'/tmp/中文 工作目录');
 assert.equal(await workspace.getAttribute('aria-expanded'),'false');
 await workspace.click();
 await page.locator('.thread-select:visible').waitFor();
 assert.equal(await page.locator('.thread-select:visible').count(),1);
 const screenshotDir=await mkdtemp(path.join(os.tmpdir(),'corki-sidebar-'));
 await page.screenshot({path:path.join(screenshotDir,'sidebar.png')});
 console.log('Sidebar screenshot:',path.join(screenshotDir,'sidebar.png'));
 await workspace.click();
 assert.equal(await page.locator('.thread-select:visible').count(),0);
 await recent.click();
 const provider=page.getByRole('button',{name:'选择供应商',exact:true});
 assert.equal(await provider.locator('.picker-fallback').innerText(),'F');
 await provider.click();assert.equal(await page.getByRole('option',{name:'fixture',exact:true}).locator('.picker-fallback').innerText(),'F');
 const fallbackRow=page.getByRole('option',{name:'fixture',exact:true});
 const brandRow=page.getByRole('option',{name:'qwen',exact:true});
 const fallbackBox=await fallbackRow.locator('.picker-icon').boundingBox();
 const brandBox=await brandRow.locator('.picker-icon').boundingBox();
 assert.equal(fallbackBox.width,20);assert.equal(fallbackBox.height,20);
 assert.equal(brandBox.width,20);assert.equal(brandBox.height,20);
 assert.equal((await fallbackRow.locator('.picker-label').boundingBox()).x,(await brandRow.locator('.picker-label').boundingBox()).x);
 await page.screenshot({path:path.join(os.tmpdir(),'corki-provider-alignment.png')});
 await page.keyboard.press('Escape');
 assert.equal(await page.getByRole('button',{name:'选择模型',exact:true}).locator('.picker-fallback svg').count(),1);
 const userAvatar=await page.locator('.message.user .avatar').boundingBox();
 const userText=await page.locator('.message.user .user-text').boundingBox();
 assert(userAvatar.x>=userText.x+userText.width);
 const agentAvatar=await page.locator('.message.assistant .avatar').boundingBox();
 const agentText=await page.locator('.message.assistant .message-content').boundingBox();
 assert(agentAvatar.x+agentAvatar.width<=agentText.x);
 await page.screenshot({path:path.join(os.tmpdir(),'corki-chat-alignment.png')});

 assert.equal(await page.locator('.markdown script, .markdown img, .markdown iframe, .markdown svg, .markdown math').count(),0);
 assert.equal(await page.evaluate(()=>window.injected),undefined);
 assert.equal(await page.locator('.markdown table td').count(),2);
 assert((await page.locator('.markdown code .hljs-keyword').count())>0);
 assert.equal(await page.getByText('恶意链接',{exact:true}).getAttribute('href'),null);
 assert.deepEqual(outbound,[]);
 assert.deepEqual(await page.evaluate(()=>window.links),[]);
 await page.getByRole('link',{name:'正常链接'}).click();
 assert.deepEqual(await page.evaluate(()=>window.links),['https://example.invalid/document']);
 await page.getByRole('button',{name:'复制回答原文'}).click();
 assert.equal(await page.evaluate(()=>window.copied),await page.evaluate(()=>window.payload));
 const codeCopy=page.getByRole('button',{name:'复制代码',exact:true});
 assert(await codeCopy.evaluate(button=>{
   const box=button.closest('.code-block').getBoundingClientRect(), rect=button.getBoundingClientRect();
   return rect.top>=box.top && rect.right<=box.right && rect.top-box.top<16 && box.right-rect.right<20 && getComputedStyle(button.closest('.code-block')).paddingTop==='0px';
 }));
 await codeCopy.click();
 assert.equal(await page.evaluate(()=>window.copied),'const text = "<script>literal</script>";\n');
 await page.locator('.new-chat').click();
 const directory=page.locator('.directory-bar');await directory.click();
 await page.getByRole('textbox',{name:'工作目录路径'}).waitFor();
 assert.equal(await page.getByRole('textbox',{name:'工作目录路径'}).evaluate(n=>n===document.activeElement),true);
 for(let i=0;i<7;i++){
  await page.keyboard.press('Tab');
  assert.equal(await page.evaluate(()=>!!document.activeElement.closest('dialog')),true);
 }
 for(let i=0;i<7;i++){await page.keyboard.press('Shift+Tab');assert.equal(await page.evaluate(()=>!!document.activeElement.closest('dialog')),true);}
 await page.keyboard.press('Escape');
 assert.equal(await page.locator('dialog').count(),0);
 assert.equal(await directory.evaluate(n=>n===document.activeElement),true);
 console.log('PASS safe Markdown/table/highlight/raw and code copy, blocked remote content/scripts, explicit links, modal autofocus/tab containment/Escape/focus restoration');
}finally{await browser.close();server.close();}
