import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {mkdtemp,rm,readdir,readFile} from 'node:fs/promises';
import path from 'node:path';import os from 'node:os';import {pathToFileURL} from 'node:url';
const tmp=await mkdtemp(path.join(os.tmpdir(),'corki-paste-unit-'));
await build({entryPoints:['electron/pasted-images.ts'],outfile:path.join(tmp,'images.mjs'),bundle:true,platform:'node',format:'esm'});
const {importPastedImages}=await import(pathToFileURL(path.join(tmp,'images.mjs')));
test.after(()=>rm(tmp,{recursive:true,force:true}));
const image=(size=10)=>({name:'截图.png',mime:'image/png',bytes:new ArrayBuffer(size)});
test('invalid clipboard payloads fail before writing any assets',async()=>{
 const home=path.join(tmp,'invalid');
 for(const value of [null,[],Array(9).fill(image()),[image(0)],[{...image(),mime:'image/svg+xml'}],[{...image(),bytes:'not bytes'}],[image(),{...image(),name:1}]])
  await assert.rejects(importPastedImages(home,'app://corki',value));
 await assert.rejects(readdir(home),{code:'ENOENT'});
});
test('clipboard assets retain original bytes and use compact persistent references',async()=>{
 const home=path.join(tmp,'valid'),payload=image(2_000_000);
 new Uint8Array(payload.bytes).fill(42);
 const [asset]=await importPastedImages(home,'app://corki',[payload]);
 assert.equal(asset.type,'image');assert.equal(asset.name,'截图.png');
 assert.equal(asset.data,`app://corki/attachments/${asset.assetId}`);
 assert(JSON.stringify(asset).length<400);
 assert.deepEqual(await readFile(path.join(home,'app/attachments',asset.assetId+'.bin')),Buffer.from(payload.bytes));
});
