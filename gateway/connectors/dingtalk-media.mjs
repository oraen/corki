import {createHash,randomUUID} from 'node:crypto';
import {mkdir,writeFile,rename,unlink} from 'node:fs/promises';
import path from 'node:path';

const MAX_IMAGES=8,MAX_BYTES=16*1024*1024;
export function messageContent(data) {
  const references=[],positions=[];
  let text='',error='',unsupported=false;
  const image=code=>{
    if(typeof code!=='string' || !code || code.length>16384){error='图片消息缺少有效的下载码';return;}
    if(references.length>=MAX_IMAGES){error='一条消息最多支持 8 张图片';return;}
    if(text && !text.endsWith('\n'))text+='\n';
    positions.push(Array.from(text).length);
    references.push({download_code:code});
    text+=`[Image #${references.length}]\n`;
  };
  if(data.msgtype==='text')text=typeof data.text?.content==='string'?data.text.content:'';
  else if(data.msgtype==='picture')image(data.content?.downloadCode);
  else if(data.msgtype==='richText'){
    if(!Array.isArray(data.content?.richText))error='图文消息格式无效';
    else for(const part of data.content.richText){
      if(typeof part.text==='string')text+=part.text;
      else if(part.type==='picture')image(part.downloadCode);
      else {unsupported=true;error='图文消息中包含暂不支持的附件';}
    }
  }else return null;
  return {text:text || (error?'[图片无法读取]':''),image_refs:references,image_positions:positions,
    ...(error?{media_error:error}:{}),...(unsupported?{unsupported:true}:{})};
}

export function downloadURL(value) {
  const u=new URL(value);
  // DingTalk's official download API can return OSS HTTP links. Upgrade only
  // recognized OSS bucket endpoints; never transfer image bytes over HTTP.
  if(u.protocol==='http:' && (!u.port || u.port==='80') &&
     /^[a-z0-9-]+\.oss-[a-z0-9-]+\.aliyuncs\.com$/i.test(u.hostname)) {
    u.protocol='https:';u.port='';
  }
  if(u.protocol!=='https:' || u.username || u.password || u.hash || (u.port && u.port!=='443') ||
     !['dingtalk.com','alicdn.com','aliyuncs.com'].some(d=>u.hostname===d || u.hostname.endsWith('.'+d)))
    throw new Error('图片下载地址不受支持');
  return u.href;
}

export function imageDownloads({directory,request,signal}) {
  async function one(ref) {
    let temporary;
    try{
      if(!directory)throw new Error('图片存储目录不可用');
      if(typeof ref?.download_code!=='string' || !ref.download_code || ref.download_code.length>16384)
        throw new Error('图片下载码无效');
      const result=await request(ref.download_code);
      let url=downloadURL(result.downloadUrl),response;
      const timeout=AbortSignal.any([signal,AbortSignal.timeout(30000)].filter(Boolean));
      for(let redirects=0;redirects<=3;redirects++){
        response=await fetch(url,{redirect:'manual',signal:timeout});
        if(![301,302,303,307,308].includes(response.status))break;
        await response.body?.cancel();
        if(redirects===3)throw new Error('图片下载跳转次数过多');
        url=downloadURL(new URL(response.headers.get('location'),url).href);
      }
      if(!response.ok)throw Object.assign(new Error('图片下载请求失败'),{httpStatus:response.status});
      if(Number(response.headers.get('content-length'))>MAX_BYTES){
        await response.body?.cancel();throw new Error('图片超过 16 MB，请压缩后重发');
      }
      const reader=response.body.getReader(),parts=[];let size=0;
      try{
        while(true){
          const {done,value}=await reader.read();if(done)break;
          size+=value.byteLength;
          if(size>MAX_BYTES){await reader.cancel();throw new Error('图片超过 16 MB，请压缩后重发');}
          parts.push(value);
        }
      }finally{reader.releaseLock();}
      if(!size)throw new Error('下载的图片为空');
      const raw=Buffer.concat(parts),id=createHash('sha256').update(raw).digest('hex');
      const root=path.join(directory,'images');await mkdir(root,{recursive:true,mode:0o700});
      temporary=path.join(root,randomUUID()+'.tmp');await writeFile(temporary,raw,{mode:0o600,flag:'wx'});
      await rename(temporary,path.join(root,id+'.bin'));temporary=null;
      return {id,size};
    }catch(error){
      let body={};try{body=JSON.parse(error.response?.data || '{}');}catch{}
      const code=String(body.code || body.errcode || '');
      const status=error.httpStatus || error.response?.status;
      const local=/^(图片|下载的图片|图文)/.test(error.message)?error.message:'图片下载失败，请检查网络和机器人文件下载权限';
      return {error:local+(status?`（HTTP ${status}）`:'')+(/^[A-Za-z0-9_.-]{1,100}$/.test(code)?` ${code}`:'')};
    }finally{if(temporary)await unlink(temporary).catch(()=>{});}
  }
  return async refs=>{
    if(!Array.isArray(refs) || refs.length>MAX_IMAGES)throw new Error('图片数量无效');
    const output=[];
    for(let i=0;i<refs.length;i+=2)output.push(...await Promise.all(refs.slice(i,i+2).map(one)));
    return {images:output};
  };
}
