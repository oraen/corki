import {poll} from './wait.mjs';
import {_electron as electron} from 'playwright';
import http from 'node:http';
import {mkdtemp,mkdir,writeFile,readFile} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import os from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
const home=await mkdtemp(path.join(os.tmpdir(),'corki-attachments-'));
const python=path.resolve('../.venv/bin/python');
const imagePath=path.join(home,'原图 测试.png'),textPath=path.join(home,'说明 文本.md'),badPath=path.join(home,'损坏.png');
execFileSync(python,['-c','import os,sys;from PIL import Image;Image.frombytes("RGB",(500,500),os.urandom(750000)).save(sys.argv[1])',imagePath]);
const original=await readFile(imagePath);assert(original.length>500000 && original.length<1000000);
await writeFile(textPath,'# 附件说明\n这是文本附件的完整内容。');await writeFile(badPath,'not a valid image');
const reply='# 附件已收到\n\n'+ '长文本🦀 '.repeat(7000);
let calls=0,receivedImage=false,receivedText=false;
const server=http.createServer(async(req,res)=>{
 try{
  let raw='';for await(const chunk of req)raw+=chunk;
  const body=JSON.parse(raw);calls++;
  const content=body.messages.findLast(m=>m.role==='user').content;
  for(const part of content){
   if(part.type==='image_url')receivedImage=createHash('sha256').update(Buffer.from(part.image_url.url.split(',')[1],'base64')).digest('hex')===createHash('sha256').update(original).digest('hex');
   if(part.type==='text')receivedText=part.text.includes('这是文本附件的完整内容。');
  }
  res.writeHead(200,{'Content-Type':'text/event-stream'});
  for(let at=0;at<reply.length;at+=2000)res.write(`data: ${JSON.stringify({choices:[{index:0,delta:{content:reply.slice(at,at+2000)},finish_reason:null}]})}\n\n`);
  res.end('data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n');
 }catch(error){console.error(error);res.destroy(error)}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const provider={base_url:`http://127.0.0.1:${server.address().port}/v1`,api_key:'fixture-not-a-credential',api_mode:'chat_completions',models:{fixture:{context_window:262144}}};
await writeFile(path.join(home,'settings.json'),JSON.stringify({default:{provider:'vision',model:'fixture'},providers:{vision:provider,text:{...provider,supports_image_input:false}},skills:{enabled:false},features:{goals:false},memories:{generate:false,use:false}}));
let app;
try{
 const env={...process.env,CORKI_HOME:home,CORKI_PYTHON:python};delete env.ELECTRON_RUN_AS_NODE;
 app=await electron.launch({args:[path.resolve('.')],env});
 await app.evaluate(({dialog})=>{dialog.showMessageBox=async()=>({response:1,checkboxChecked:false})});
 const page=await app.firstWindow();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.getByRole('textbox',{name:'消息',exact:true}).fill('请查看附件');
 const choose=async paths=>{
  await app.evaluate(({dialog},paths)=>{dialog.showOpenDialog=async()=>({canceled:false,filePaths:paths})},paths);
  await page.getByRole('button',{name:'添加附件',exact:true}).click();
 };
 await choose([imagePath]);
 await page.getByRole('button',{name:'移除 原图 测试.png'}).waitFor();
 // Real File objects populated by Chromium retain paths for Electron webUtils.
 await page.evaluate(()=>{const input=document.createElement('input');input.type='file';input.id='fixture-drop';document.body.append(input)});
 await page.locator('#fixture-drop').setInputFiles(textPath);
 await page.evaluate(()=>{const transfer=new DataTransfer();transfer.items.add(document.querySelector('#fixture-drop').files[0]);document.querySelector('.composer').dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:transfer}));document.querySelector('#fixture-drop').remove()});
 await page.getByRole('button',{name:'移除 说明 文本.md'}).waitFor();
 await poll(()=>page.evaluate(async()=>{
  const draft=await window.corki.request('state.read',{key:'draft:new'});return draft?.attachments.length===2;
 }));
 const stored=await page.evaluate(()=>window.corki.request('state.read',{key:'draft:new'}));
 assert(JSON.stringify(stored).length<2000);assert(stored.attachments.every(a=>a.assetId));
 await page.reload();await page.getByRole('button',{name:'移除 原图 测试.png'}).waitFor();
 await page.getByRole('button',{name:'说明 文本.md',exact:true}).click();
 await page.locator('.text-preview').filter({hasText:'这是文本附件的完整内容。'}).waitFor();
 await page.getByRole('button',{name:'关闭',exact:true}).click();
 await page.getByRole('button',{name:'选择供应商',exact:true}).click();
 await page.getByRole('option',{name:'text',exact:true}).click();
 await page.getByRole('button',{name:'发送消息'}).click();
 await page.locator('.inline-error').filter({hasText:'未声明支持图片'}).waitFor();assert.equal(calls,0);
 await page.getByRole('button',{name:'选择供应商',exact:true}).click();await page.getByRole('option',{name:'vision',exact:true}).click();
 await choose([badPath]);await page.getByRole('button',{name:'移除 损坏.png'}).waitFor();
 await page.getByRole('button',{name:'发送消息'}).click();
 await page.locator('.inline-error').filter({hasText:'图片内容无效'}).waitFor();assert.equal(calls,0);
 await page.getByRole('button',{name:'移除 损坏.png'}).click();
 await page.getByRole('button',{name:'发送消息'}).click();
 await page.locator('.markdown h1').filter({hasText:'附件已收到'}).waitFor({timeout:30000});
 assert(receivedImage && receivedText);assert.equal(calls,1);
 await page.getByRole('button',{name:'复制回答原文'}).waitFor();
 await page.reload();
 await page.locator('.history-image').scrollIntoViewIfNeeded();
 await page.getByRole('button',{name:'查看图片 1'}).waitFor({timeout:30000});
 await page.getByRole('button',{name:'展开更多'}).last().waitFor();
 await page.getByRole('button',{name:'复制回答原文'}).click();
 await page.getByRole('button',{name:'复制回答原文'}).filter({hasText:'已复制'}).waitFor();
 assert.equal(await app.evaluate(({clipboard})=>clipboard.readText()),reply);
 await page.getByRole('button',{name:'查看图片 1'}).click();await page.locator('.image-preview').waitFor();
 await page.screenshot({path:'/tmp/corki-attachment-history.png'});
 assert.deepEqual(errors,[]);
 console.log('PASS large image reference draft/reload, actual drag/drop, text preview, model rejection, malformed image, full original input, lazy history image and full Unicode answer copy');
}catch(error){if(app)console.error((await (await app.firstWindow()).locator('body').innerText()).slice(-2500));throw error}
finally{if(app)await app.close();server.closeAllConnections();server.close();console.log('Disposable test home:',home)}
