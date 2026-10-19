import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {mkdtemp,rm} from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {pathToFileURL} from 'node:url';
const tmp=await mkdtemp(path.join(os.tmpdir(),'corki-fragments-'));
await build({entryPoints:['electron/framing.ts'],outfile:path.join(tmp,'framing.mjs'),bundle:true,platform:'node',format:'esm'});
const {MessageAssembler,MAX_MESSAGE}=await import(pathToFileURL(path.join(tmp,'framing.mjs')));
test.after(()=>rm(tmp,{recursive:true,force:true}));
const id='a'.repeat(32);
const frame=(text,index,bytes,last=false,extra={})=>JSON.stringify({jsonrpc:'2.0',method:'transport.chunk',params:{id,index,bytes,last,text,...extra}});
test('reassembles Unicode JSON exactly and releases the previous message',()=>{
 const a=new MessageAssembler(()=>assert.fail('unexpected timeout'));
 const raw=JSON.stringify({jsonrpc:'2.0',id:1,result:{text:'中文🦀'.repeat(100000)}}),bytes=Buffer.byteLength(raw);
 try {
  let result;
  // Split by code point just as Python does; preserve surrogate pairs.
  const chars=Array.from(raw);
  for(let at=0,index=0;at<chars.length;at+=128000,index++)result=a.accept(frame(chars.slice(at,at+128000).join(''),index,bytes,at+128000>=chars.length));
  assert.deepEqual(result,JSON.parse(raw));
  assert.deepEqual(a.accept('{"jsonrpc":"2.0","id":2,"result":{}}'),{jsonrpc:'2.0',id:2,result:{}});
 } finally {a.clear()}
});
test('rejects sequence gaps, duplicates, oversized budgets, truncated and interleaved messages',()=>{
 for(const follow of [frame('}',2,2,true),frame('}',0,2,true),frame('}',1,3,true),frame('}',1,2,true,{id:'b'.repeat(32)}),'{}']){
  const a=new MessageAssembler(()=>{});
  try{a.accept(frame('{',0,2));assert.throws(()=>a.accept(follow))}finally{a.clear()}
 }
 for(const bad of [frame('{',0,MAX_MESSAGE+1),frame('{}',0,1,true),frame('{',0,5,true),frame('{}',0,2),frame('x',1,2),frame('x',0,2,false,{id:'invalid'})]){
  const a=new MessageAssembler(()=>{});try{assert.throws(()=>a.accept(bad))}finally{a.clear()}
 }
});
test('abandoned assembly expires once and explicit disposal releases the timer',async()=>{
 let expired=0;
 const a=new MessageAssembler(()=>expired++,15);
 a.accept(frame('{',0,2));
 await new Promise(resolve=>setTimeout(resolve,40));assert.equal(expired,1);
 assert.deepEqual(a.accept('{}'),{});
 a.accept(frame('{',0,2));a.clear();
 await new Promise(resolve=>setTimeout(resolve,40));assert.equal(expired,1);
});
