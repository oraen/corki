import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, mkdir, readFile, writeFile, rm} from 'node:fs/promises';
import {createHash, createCipheriv, createDecipheriv} from 'node:crypto';
import os from 'node:os';
import path from 'node:path';
import {createMedia, cdnUrl, imageRef, fileRef, MAX_IMAGE_BYTES} from '../connectors/wechat-media.mjs';
import {create as wechat} from '../connectors/wechat.mjs';

test('WeChat video/file upload and encrypted download preserve content; typing lifecycle',async()=>{
  const directory=await mkdtemp(path.join(os.tmpdir(),'corki-wx-files-'));
  const original=globalThis.fetch,source=await readFile(new URL('./fixtures/wechat-video.mp4',import.meta.url));
  const document=Buffer.from('你好，这是文件内容。'),sent=[],uploads=[],indicators=[];
  const key=Buffer.alloc(16,23),cipher=createCipheriv('aes-128-ecb',key,null);
  const encrypted=Buffer.concat([cipher.update(source),cipher.final()]);
  let adapter,configCalls=0,failTyping=false,lastUpload;
  try{
    await mkdir(path.join(directory,'files'));
    await writeFile(path.join(directory,'login.json'),JSON.stringify({id:'bot',token:'secret',base:'https://ilinkai.weixin.qq.com'}));
    const files=[];
    for(const [kind,raw,name] of [['video',source,'演示.mp4'],['file',document,'说明.txt']]){
      const id=createHash('sha256').update(raw).digest('hex');
      await writeFile(path.join(directory,'files',id+'.bin'),raw);files.push({id,kind,name});
    }
    globalThis.fetch=async(url,options)=>{
      if(url.includes('getconfig')){configCalls++;return new Response(JSON.stringify({typing_ticket:'private-ticket'}));}
      if(url.includes('sendtyping')){if(failTyping)throw new Error('network');indicators.push(JSON.parse(options.body).status);return new Response('');}
      if(url.includes('getuploadurl')){lastUpload=JSON.parse(options.body);uploads.push(lastUpload);return new Response('{"upload_param":"private"}');}
      if(url.includes('/c2c/upload')){
        const decrypt=createDecipheriv('aes-128-ecb',Buffer.from(lastUpload.aeskey,'hex'),null);
        const restored=Buffer.concat([decrypt.update(options.body),decrypt.final()]);
        assert.deepEqual(restored,lastUpload.media_type===2?source:document);
        return new Response('',{headers:{'x-encrypted-param':'receipt'}});
      }
      if(url.includes('/c2c/download'))return new Response(encrypted);
      if(url.includes('sendmessage')){sent.push(JSON.parse(options.body).msg);return new Response('{}');}
      throw new Error('unexpected endpoint');
    };
    adapter=await wechat({state_directory:directory,incoming:async()=>({accepted:true})});
    await adapter.send({message:{conversation:'peer'},text:'',files});
    assert.deepEqual(uploads.map(u=>u.media_type),[2,3]);
    assert.deepEqual(sent.map(m=>m.item_list[0].type),[5,4]);
    assert.equal(sent[1].item_list[0].file_item.file_name,'说明.txt');
    assert.equal(sent[1].item_list[0].file_item.len,String(document.length));
    const ref=fileRef({type:5,video_item:{media:{encrypt_query_param:'private',aes_key:key.toString('base64')},video_md5:createHash('md5').update(source).digest('hex')}});
    const received=await adapter.downloadFiles([ref]);
    assert.deepEqual(await readFile(path.join(directory,'files',received.images[0].id+'.bin')),source);
    const bad=await adapter.downloadFiles([{...ref,md5:'11'.repeat(16)}]);
    assert.match(bad.images[0].error,/校验/);
    const message={id:'one',conversation:'peer',context_token:'private-context'};
    assert.equal((await adapter.typing({message,active:true})).supported,true);
    await adapter.typing({message,active:true});
    await adapter.typing({message:{...message,id:'two'},active:true});
    await adapter.typing({message,active:false});
    assert.deepEqual(indicators,[1,1,1]);
    await adapter.typing({message:{...message,id:'two'},active:false});
    assert.deepEqual(indicators,[1,1,1,2]);
    assert.equal(configCalls,1);
    failTyping=true;
    assert.equal((await adapter.typing({message,active:true})).supported,false);
    await adapter.send({message,text:'Still works'});
  }finally{await adapter?.close();globalThis.fetch=original;await rm(directory,{recursive:true,force:true});}
});

test('WeChat adapter uploads before sending caption and images, refuses empty messages',async()=>{
  const directory=await mkdtemp(path.join(os.tmpdir(),'corki-wx-send-'));
  const original=globalThis.fetch,raw=Buffer.from('fixture-image');
  const id=createHash('sha256').update(raw).digest('hex');
  let adapter,failUpload=false;
  const sent=[],sequence=[];
  try{
    await mkdir(path.join(directory,'images'));
    await writeFile(path.join(directory,'images',id+'.bin'),raw);
    await writeFile(path.join(directory,'login.json'),JSON.stringify({id:'bot',token:'secret',base:'https://ilinkai.weixin.qq.com'}));
    globalThis.fetch=async(url,options)=>{
      if(url.includes('getuploadurl')){sequence.push('prepare');return new Response(JSON.stringify({ret:0,upload_param:'private'}));}
      if(url.includes('/c2c/upload')){sequence.push('upload');return new Response('',{headers:failUpload?{}:{'x-encrypted-param':'receipt'}});}
      if(url.includes('sendmessage')){sequence.push('send');sent.push(JSON.parse(options.body).msg);return new Response('{"ret":0}');}
      throw new Error('unexpected request');
    };
    adapter=await wechat({state_directory:directory,incoming:async()=>({accepted:true})});
    await adapter.send({message:{conversation:'peer',context_token:'context'},text:'caption',images:[{id}]});
    assert.deepEqual(sequence,['prepare','upload','send','send']);
    assert.equal(sent[0].item_list[0].text_item.text,'caption');
    assert.equal(sent[1].item_list[0].type,2);
    assert.equal(sent[1].context_token,'context');
    failUpload=true;
    await assert.rejects(adapter.send({message:{conversation:'peer'},text:'caption',images:[{id}]}));
    assert.equal(sent.length,2);
    await assert.rejects(adapter.send({message:{conversation:'peer'},text:''}),/请输入/);
  }finally{await adapter?.close();globalThis.fetch=original;await rm(directory,{recursive:true,force:true});}
});

test('WeChat encrypted CDN round trip, key formats and failure boundaries', async () => {
  const directory=await mkdtemp(path.join(os.tmpdir(),'corki-wx-media-'));
  const original=globalThis.fetch, controller=new AbortController();
  const raw=Buffer.from('fixture image contents'),key=Buffer.alloc(16,17);
  const cipher=createCipheriv('aes-128-ecb',key,null);
  const encrypted=Buffer.concat([cipher.update(raw),cipher.final()]);
  const id=createHash('sha256').update(raw).digest('hex');
  let response=()=>new Response(encrypted), requests=[], uploadRequest;
  globalThis.fetch=async(url,options)=>{requests.push({url,options});return response();};
  const media=createMedia({directory,signal:controller.signal,api:async body=>{
    uploadRequest=body;return {upload_param:'private-upload-param'};
  }});
  try {
    for(const ref of [{aes_hex:key.toString('hex')},
      {aes_key:key.toString('base64')},
      {aes_key:Buffer.from(key.toString('hex')).toString('base64')}]) {
      const result=await media.download([{query:'private-query',...ref}]);
      assert.equal(result.images[0].id,id);
      assert.deepEqual(await readFile(path.join(directory,'images',id+'.bin')),raw);
      assert.equal(requests.at(-1).options.redirect,'error');
    }
    response=()=>new Response(raw);
    assert.equal((await media.download([{query:'plain'}])).images[0].id,id);
    response=()=>new Response('',{headers:{'x-encrypted-param':'private-receipt'}});
    const [item]=await media.upload([{id}],'peer');
    assert.equal(uploadRequest.to_user_id,'peer');
    assert.equal(uploadRequest.rawsize,raw.length);
    assert.equal(uploadRequest.rawfilemd5,createHash('md5').update(raw).digest('hex'));
    assert.equal(uploadRequest.media_type,1);
    assert.equal(uploadRequest.no_need_thumb,true);
    assert.equal(item.type,2);
    const sentKey=Buffer.from(Buffer.from(item.image_item.media.aes_key,'base64').toString(),'hex');
    const decrypt=createDecipheriv('aes-128-ecb',sentKey,null);
    const posted=requests.at(-1).options.body;
    assert.deepEqual(Buffer.concat([decrypt.update(posted),decrypt.final()]),raw);
    assert.equal(uploadRequest.filesize,posted.length);
    assert.equal(item.image_item.media.encrypt_query_param,'private-receipt');
    response=()=>new Response('');
    await assert.rejects(media.upload([{id}],'peer'),/上传确认/);
    await assert.rejects(media.upload([{id:'../outside'}],'peer'),/标识/);
    await assert.rejects(media.download(Array(9).fill({})),/数量/);
    for(const url of ['http://novac2c.cdn.weixin.qq.com/c2c/x',
      'https://evil.test/c2c/x','https://novac2c.cdn.weixin.qq.com.evil.test/c2c/x',
      'https://user:pass@novac2c.cdn.weixin.qq.com/c2c/x',
      'https://novac2c.cdn.weixin.qq.com/other'])assert.throws(()=>cdnUrl(url));
    assert.throws(()=>imageRef({type:2}),/缺失/);
    let result=await media.download([{query:'secret',aes_key:'bad'}]);
    assert.match(result.images[0].error,/凭据/);
    response=()=>new Response('x',{headers:{'content-length':String(MAX_IMAGE_BYTES+17)}});
    result=await media.download([{query:'secret'}]);
    assert.match(result.images[0].error,/16 MB/);
    globalThis.fetch=async()=>{throw new Error('redirect failed private-query');};
    result=await media.download([{query:'secret'}]);
    assert.match(result.images[0].error,/下载或解密失败/);
    assert(!JSON.stringify(result).includes('private'));
    controller.abort();
    globalThis.fetch=async(_url,options)=>{options.signal.throwIfAborted();};
    assert.match((await media.download([{query:'secret'}])).images[0].error,/失败/);
  } finally {globalThis.fetch=original;await rm(directory,{recursive:true,force:true});}
});
