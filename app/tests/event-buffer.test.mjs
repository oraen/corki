import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {mkdtemp,rm} from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {pathToFileURL} from 'node:url';
const tmp=await mkdtemp(path.join(os.tmpdir(),'corki-event-buffer-'));
await build({entryPoints:['renderer/event-buffer.ts'],outfile:path.join(tmp,'buffer.mjs'),bundle:true,format:'esm',platform:'node'});
const {EventBuffer}=await import(pathToFileURL(path.join(tmp,'buffer.mjs')));
test.after(()=>rm(tmp,{recursive:true,force:true}));
const event=(sequence,text='',instance='one')=>({sequence,instance,type:'AssistantTextDelta',threadId:'thread',data:{delta:text}});
test('bounded count retains exact boundary and detects an evicted prefix',()=>{
 const b=new EventBuffer();for(let i=1;i<=513;i++)b.push(event(i));
 assert.equal(b.after('one',0),null);assert.equal(b.after('one',1).length,512);
 assert.deepEqual(b.after('one',512).map(e=>e.sequence),[513]);
 b.push(event(513));assert.equal(b.after('one',1).length,512);
});
test('byte budget and sequence gaps require authoritative resynchronization',()=>{
 const b=new EventBuffer();b.push(event(1));b.push(event(2,'文'.repeat(400000)));
 assert.equal(b.after('one',1),null);assert.deepEqual(b.after('one',2),[]);
 b.push(event(5));assert.equal(b.after('one',3),null);
 assert.deepEqual(b.after('one',4).map(e=>e.sequence),[5]);
});
test('host epochs never mix and snapshots ahead of delivered events remain valid',()=>{
 const b=new EventBuffer();assert.deepEqual(b.after('one',10),[]);
 b.push(event(1));assert.deepEqual(b.after('one',10),[]);
 b.push(event(1,'new','two'));assert.equal(b.after('one',1),null);
 assert.equal(b.after('two',0)[0].data.delta,'new');b.clear();assert.deepEqual(b.after('two',0),[]);
});
