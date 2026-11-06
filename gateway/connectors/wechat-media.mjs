// Tencent iLink 2.4.8 image wire format; independent bounded CDN transport.
import { createHash, createCipheriv, createDecipheriv, randomBytes, randomUUID } from "node:crypto";
import { mkdir, readFile, writeFile, rename, unlink, lstat } from "node:fs/promises";
import path from "node:path";

export const MAX_IMAGE_BYTES = 16 * 1024 * 1024;
export const MAX_FILE_BYTES = 32 * 1024 * 1024;
const CDN = "https://novac2c.cdn.weixin.qq.com/c2c";
const ID = /^[a-f0-9]{64}$/;

export function cdnUrl(value) {
  const url = new URL(value);
  if (url.protocol !== "https:" || url.username || url.password || url.port ||
      !(url.hostname === "novac2c.cdn.weixin.qq.com" || url.hostname.endsWith(".cdn.weixin.qq.com")) ||
      !url.pathname.startsWith("/c2c/")) throw new Error("图片 CDN 地址无效");
  return url.toString();
}

export function imageRef(item) {
  const image = item?.image_item, media = image?.media;
  if (!media || (!media.full_url && !media.encrypt_query_param)) throw new Error("图片下载信息缺失");
  const ref = {query:media.encrypt_query_param || "",url:media.full_url || "",
    aes_key:media.aes_key || "",aes_hex:image.aeskey || ""};
  if (Object.values(ref).some(v=>typeof v!=="string" || v.length>16384)) throw new Error("图片下载信息无效");
  if(ref.url)cdnUrl(ref.url);
  return ref;
}

export function fileRef(item) {
  const content=item.type===5?item.video_item:item.file_item;
  const ref=imageRef({image_item:content});
  const name=item.type===5?'video.mp4':content.file_name || 'attachment';
  if(typeof name!=='string' || name.length>255)throw new Error('附件名称无效');
  return {...ref,kind:item.type===5?'video':'file',name,
    md5:content.video_md5 || content.md5 || ''};
}

function aesKey(ref) {
  if (ref.aes_hex) {
    if(!/^[a-fA-F0-9]{32}$/.test(ref.aes_hex))throw new Error("图片解密凭据无效");
    return Buffer.from(ref.aes_hex,"hex");
  }
  if(!ref.aes_key)return null;
  const value=Buffer.from(ref.aes_key,"base64");
  if(value.length===16)return value;
  if(value.length===32 && /^[a-fA-F0-9]{32}$/.test(value.toString("ascii")))return Buffer.from(value.toString("ascii"),"hex");
  throw new Error("图片解密凭据无效");
}

async function boundedBody(response,limit) {
  if(!response.ok)throw new Error("图片 CDN 请求失败");
  const declared=Number(response.headers?.get("content-length"));
  const tooLarge=limit>MAX_IMAGE_BYTES+16?'附件超过 32 MB':'图片超过 16 MB';
  if(Number.isFinite(declared) && declared>limit)throw new Error(tooLarge);
  const parts=[];let size=0;
  const reader=response.body?.getReader();
  if(!reader)throw new Error("图片响应为空");
  try {
    while(true){
      const {done,value}=await reader.read();if(done)break;
      size+=value.byteLength;
      if(size>limit)throw new Error(tooLarge);
      parts.push(Buffer.from(value));
    }
  } finally {await reader.cancel().catch(()=>{});}
  if(!size)throw new Error("下载的图片为空");
  return Buffer.concat(parts);
}

export function createMedia({directory,api,signal,attachments=false}) {
  const root=path.join(directory,attachments?'files':"images");
  const limit=attachments?MAX_FILE_BYTES:MAX_IMAGE_BYTES;
  const deadline=()=>AbortSignal.any([signal,AbortSignal.timeout(60000)]);
  async function download(ref) {
    let temporary;
    try {
      if(!ref || typeof ref!=="object")throw new Error("图片下载信息无效");
      const url=cdnUrl(ref.url || `${CDN}/download?encrypted_query_param=${encodeURIComponent(ref.query || "")}`);
      const key=aesKey(ref);
      const response=await fetch(url,{redirect:"error",signal:deadline()});
      const encrypted=await boundedBody(response,limit+16);
      let raw=encrypted;
      if(key){
        const cipher=createDecipheriv("aes-128-ecb",key,null);
        raw=Buffer.concat([cipher.update(encrypted),cipher.final()]);
      }
      if((!raw.length && !attachments) || raw.length>limit)throw new Error(attachments?'附件超过 32 MB':"图片内容为空或超过 16 MB");
      if(ref.md5 && (!/^[a-fA-F0-9]{32}$/.test(ref.md5) || createHash('md5').update(raw).digest('hex')!==ref.md5.toLowerCase()))throw new Error('附件内容校验失败');
      const id=createHash("sha256").update(raw).digest("hex");
      await mkdir(root,{recursive:true,mode:0o700});
      temporary=path.join(root,randomUUID()+".tmp");
      await writeFile(temporary,raw,{flag:"wx",mode:0o600});
      await rename(temporary,path.join(root,id+".bin"));temporary=null;
      return {id,size:raw.length};
    } catch(error) {
      const reason=/^(图片|附件|下载的图片)/.test(error.message)?error.message:"图片下载或解密失败，请重发图片";
      return {error:attachments?reason.replaceAll('图片','附件'):reason};
    } finally {if(temporary)await unlink(temporary).catch(()=>{});}
  }

  async function upload(image,recipient) {
    const id=image?.id;
    if(typeof id!=="string" || !ID.test(id))throw new Error("图片标识无效");
    const file=path.join(root,id+".bin"),info=await lstat(file);
    if(!info.isFile() || info.isSymbolicLink() || info.size>limit)throw new Error("附件不存在或已清理");
    const raw=await readFile(file);
    if((!raw.length && !attachments) || raw.length>limit || createHash("sha256").update(raw).digest("hex")!==id)
      throw new Error("图片内容校验失败");
    const key=randomBytes(16),filekey=randomBytes(16).toString("hex");
    const cipher=createCipheriv("aes-128-ecb",key,null);
    const encrypted=Buffer.concat([cipher.update(raw),cipher.final()]);
    const kind=attachments?image.kind:'image';
    if(!['image','file','video'].includes(kind))throw new Error('附件类型无效');
    const value=await api({filekey,media_type:kind==='image'?1:kind==='video'?2:3,to_user_id:recipient,rawsize:raw.length,
      rawfilemd5:createHash("md5").update(raw).digest("hex"),filesize:encrypted.length,
      no_need_thumb:true,aeskey:key.toString("hex")});
    if(!value.upload_full_url && !value.upload_param)throw new Error("图片上传地址缺失");
    const url=cdnUrl(value.upload_full_url || `${CDN}/upload?encrypted_query_param=${encodeURIComponent(value.upload_param)}&filekey=${filekey}`);
    const response=await fetch(url,{method:"POST",headers:{"Content-Type":"application/octet-stream"},
      body:encrypted,redirect:"error",signal:deadline()});
    const query=response.headers.get("x-encrypted-param");
    try {
      if(!response.ok || !query || query.length>16384)throw new Error("图片上传失败，未获得上传确认");
    } finally {await response.body?.cancel().catch(()=>{});}
    // Tencent's sender encodes the hex string as base64; accept both forms on receive.
    const media={encrypt_query_param:query,aes_key:Buffer.from(key.toString("hex")).toString("base64"),encrypt_type:1};
    if(kind==='video')return {type:5,video_item:{media,video_size:encrypted.length}};
    if(kind==='file')return {type:4,file_item:{media,file_name:path.basename(image.name || 'attachment').replace(/[\x00-\x1f]/g,'_').slice(0,255),len:String(raw.length)}};
    return {type:2,image_item:{media,mid_size:encrypted.length}};
  }
  return {
    async download(refs) {
      if(!Array.isArray(refs) || refs.length>8)throw new Error("图片数量无效");
      const images=[];
      for(let i=0;i<refs.length;i+=2)images.push(...await Promise.all(refs.slice(i,i+2).map(download)));
      return {images};
    },
    async upload(images,recipient) {
      if(!Array.isArray(images) || images.length>8)throw new Error("图片数量无效");
      const items=[];
      for(const image of images)items.push(await upload(image,recipient));
      return items;
    },
  };
}
