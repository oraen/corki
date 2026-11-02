import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile,rm,readdir} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import os from 'node:os';
import path from 'node:path';
import {messageContent,downloadURL,imageDownloads} from '../connectors/dingtalk-media.mjs';

test('Stream callback admits mixed content before downloading and ACKs durable admission',async()=>{
 const {create}=await import('../connectors/dingtalk.mjs');
 let callback,acked=false,received;
 class Client {
  registerCallbackListener(_topic,handler){callback=handler;}
  async connect(){this.connected=true;}
  socketCallBackResponse(){acked=true;}
  disconnect(){this.connected=false;}
 }
 const adapter=await create({config:{client_id:'fixture',client_secret:'fixture'},
  require:()=>({DWClient:Client,TOPIC_ROBOT:'robot'}),incoming:async message=>{received=message;}});
 try{
  await adapter.start();
  await callback({headers:{messageId:'fixture'},data:JSON.stringify({msgId:'fixture',senderId:'fixture-user',
   senderStaffId:'fixture-staff',conversationId:'fixture-chat',conversationType:'1',
   msgtype:'richText',content:{richText:[{text:'分析表格'},{type:'picture',downloadCode:'fixture-code'}]},
   sessionWebhook:'https://oapi.dingtalk.com/robot/sendBySession?session=fixture',sessionWebhookExpiredTime:Date.now()+60000})});
  assert(acked);assert.equal(received.image_refs.length,1);assert.match(received.text,/分析表格/);
  assert.equal(received.staff_id,'fixture-staff');assert.equal(JSON.stringify(received).includes('base64'),false);
 }finally{await adapter.close();}
});

test('picture and mixed text retain Unicode positions, image order and bounded download codes',()=>{
 const value=messageContent({msgtype:'richText',content:{richText:[{text:'你好🦀表格'},
  {type:'picture',downloadCode:'first'},{text:'再看这张'},{type:'picture',downloadCode:'second'}]}});
 assert.deepEqual(value.image_refs,[{download_code:'first'},{download_code:'second'}]);
 const chars=Array.from(value.text);
 for(const [i,p] of value.image_positions.entries())assert.equal(chars.slice(p,p+10).join(''),`[Image #${i+1}]`);
 assert.equal(messageContent({msgtype:'picture',content:{downloadCode:'fixture'}}).text,'[Image #1]\n');
 assert.match(messageContent({msgtype:'picture',content:{}}).media_error,/下载码/);
 assert.match(messageContent({msgtype:'richText',content:{richText:Array.from({length:9},()=>({type:'picture',downloadCode:'fixture'}))}}).media_error,/8/);
 assert.equal(messageContent({msgtype:'audio'}),null);
});

test('image download saves bytes to owned assets and returns metadata, never base64 or signed URL',async()=>{
 const directory=await mkdtemp(path.join(os.tmpdir(),'corki-dingtalk-media-')),original=globalThis.fetch;
 const raw=Buffer.from('fixture bytes'),calls=[];
 globalThis.fetch=async(url,options)=>{
  calls.push({url,options});
  if(calls.length===1)return new Response(null,{status:302,headers:{location:'https://fixture.oss-cn.example.aliyuncs.com/image?signature=fixture-private'}});
  return new Response(raw,{headers:{'content-type':'application/octet-stream'}});
 };
 try{
  const download=imageDownloads({directory,request:async code=>{assert.equal(code,'fixture-code');return {downloadUrl:'https://oapi.dingtalk.com/image'};}});
  const value=await download([{download_code:'fixture-code'}]);
  const id=createHash('sha256').update(raw).digest('hex');
  assert.deepEqual(value,{images:[{id,size:raw.length}]});
  assert.deepEqual(await readFile(path.join(directory,'images',id+'.bin')),raw);
  assert.equal(JSON.stringify(value).includes('fixture-private'),false);
  assert(calls.every(c=>!c.options.headers));
  assert.equal((await readdir(path.join(directory,'images'))).some(n=>n.endsWith('.tmp')),false);
 }finally{globalThis.fetch=original;await rm(directory,{recursive:true,force:true});}
});

test('untrusted redirects, excessive bytes and failed API replies become bounded image errors',async()=>{
 const directory=await mkdtemp(path.join(os.tmpdir(),'corki-dingtalk-media-')),original=globalThis.fetch;
 try{
  assert.equal(downloadURL('http://fixture-bucket.oss-cn-test.aliyuncs.com/image?signature=fixture'),
    'https://fixture-bucket.oss-cn-test.aliyuncs.com/image?signature=fixture');
  assert.throws(()=>downloadURL('http://fixture-bucket.oss-cn-test.aliyuncs.com:8080/image'));
  assert.throws(()=>downloadURL('http://fixture-bucket.oss-cn-test.aliyuncs.com.evil.invalid/image'));
  for(const url of ['http://oapi.dingtalk.com/image','https://oapi.dingtalk.com.evil.invalid/image','https://localhost/image','https://oapi.dingtalk.com@evil.invalid/image'])assert.throws(()=>downloadURL(url));
  const download=imageDownloads({directory,request:async()=>({downloadUrl:'https://oapi.dingtalk.com/image'})});
  globalThis.fetch=async()=>new Response(null,{status:302,headers:{location:'http://127.0.0.1/private'}});
  assert.match((await download([{download_code:'fixture'}])).images[0].error,/地址/);
  globalThis.fetch=async()=>new Response('small',{headers:{'content-length':String(17*1024*1024)}});
  assert.match((await download([{download_code:'fixture'}])).images[0].error,/16 MB/);
  globalThis.fetch=async()=>new Response(new ReadableStream({start(c){c.enqueue(new Uint8Array(9*1024*1024));c.enqueue(new Uint8Array(9*1024*1024));c.close();}}));
  assert.match((await download([{download_code:'fixture'}])).images[0].error,/16 MB/);
  const broken=imageDownloads({directory,request:async()=>{throw {response:{status:403,data:'{"code":"Forbidden.AccessDenied","message":"fixture-private-token"}'}};}});
  const result=await broken([{download_code:'fixture'}]);
  assert.match(result.images[0].error,/403.*Forbidden.AccessDenied/);
  assert.equal(JSON.stringify(result).includes('fixture-private-token'),false);
 }finally{globalThis.fetch=original;await rm(directory,{recursive:true,force:true});}
});
