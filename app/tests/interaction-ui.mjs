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
    if (!p.startsWith(root + "/")) throw Error();
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
const browser = await chromium.launch({
  executablePath:
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});
try {
 const page=await browser.newPage({viewport:{width:1440,height:900}});
 await page.addInitScript(()=>{
  window.responses=[];window.links=[];window.seq=0;
  const saved=new Map([['ui',{selected:'thread'}]]);
  window.corki={
   request:async(method,p)=>{
    if(method==='initialize')return{instance:'fixture',sequence:0,threadManagementVersion:2};
    if(method==='state.read')return saved.get(p.key)||null;
    if(method==='state.write'){saved.set(p.key,p.value);return{}};
    if(method==='config.read')return{valid:true,default:{provider:'fixture',model:'fixture'},providers:[{id:'fixture',models:[{id:'fixture',reasoningLevels:[],reasoning:null,images:false}]}]};
    if(method==='threads.list')return{threads:[{id:'thread',title:'交互验收'}],next:null};
    if(method==='threads.read')return{threadId:'thread',instance:'fixture',sequence:0,messages:[],next:null,turns:[],live:[],pending:[],running:true,directory:'/tmp/fixture'};
    if(method==='interaction.respond'){
     window.responses.push(p);
     window.emit({type:'interaction.resolved',threadId:'thread',instance:'fixture',sequence:++window.seq,data:{requestId:p.requestId}});return{};
    }
   },subscribe:callback=>{window.emit=callback;return()=>{}},copy:async()=>{},openLink:async url=>window.links.push(url)
  };
 });
 await page.goto(`http://127.0.0.1:${server.address().port}/index.html`);
 await page.getByRole('button',{name:'选择模型',exact:true}).filter({hasText:'fixture'}).waitFor();
 const inject=(type,data)=>page.evaluate(({type,data})=>window.emit({type,data,threadId:'thread',instance:'fixture',sequence:++window.seq}),{type,data});
 await inject('UserInputRequested',{turn_id:'turn',call_id:'question',questions:[{id:'scope',question:'选择范围',options:[{label:'仅当前目录',description:'限制范围'}]},{id:'secret',question:'测试口令',is_secret:true}]});
 await page.getByRole('button',{name:'仅当前目录',exact:true}).click();
 assert.equal(await page.getByRole('textbox',{name:'选择范围'}).inputValue(),'仅当前目录');
 await page.getByLabel('测试口令').fill('fixture-value');
 assert.equal(await page.getByLabel('测试口令').getAttribute('type'),'password');
 await page.getByRole('button',{name:'提交回答'}).click();
 assert.deepEqual(await page.evaluate(()=>window.responses.at(-1).response),{answers:{scope:{answers:['仅当前目录']},secret:{answers:['fixture-value']}}});
 await inject('approval',{threadId:'thread',request_id:'patch',kind:'patch_approval',params:{message:'确认变更示例文件',mode:'form',requestedSchema:{properties:{scope:{type:'string',enum:['once','session'],default:'once'}}},_meta:{tool_params:{patch:'*** Begin Patch\n*** Add File: fixture.txt\n+hello\n*** End Patch'},patch_retry:{message:'原有修改保留'}}}});
 await page.getByText('文件变更',{exact:true}).waitFor();
 await page.getByText('上次执行结果（已有修改未回滚）',{exact:true}).waitFor();
 await page.getByRole('button',{name:'允许本次',exact:true}).click();
 assert.deepEqual(await page.evaluate(()=>window.responses.at(-1).content),{scope:'once'});
 for(const [label,action] of [['已完成','accept'],['拒绝','decline'],['取消请求','cancel']]){
  await inject('approval',{threadId:'thread',request_id:'url-'+action,server_name:'fixture',kind:'server',params:{mode:'url',message:'在浏览器中确认后返回',url:'https://example.invalid/fixture'}});
  assert.equal(await page.evaluate(()=>window.links.length),action==='accept'?0:1);
  if(action==='accept'){
   await page.getByRole('button',{name:'打开链接'}).click();
   assert.equal(await page.evaluate(()=>window.responses.at(-1).requestId),'patch');
   assert.equal(await page.evaluate(()=>window.links.at(-1)),'https://example.invalid/fixture');
   await page.screenshot({path:'/tmp/corki-desktop-url-interaction.png'});
  }
  await page.getByRole('button',{name:label,exact:true}).click();
  const response=await page.evaluate(()=>window.responses.at(-1));
  assert.equal(response.action,action);assert(!Object.hasOwn(response,'content'));
 }
 assert.equal(await page.locator('.interaction').count(),0);
 console.log('PASS question choices/secret field and answer payload; patch review/retry details and scope; URL explicit-open, accept/decline/cancel and cleanup');
}finally{await browser.close();server.close();}
