import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {mkdtemp,rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const dir=await mkdtemp(path.join(os.tmpdir(),'corki-refresh-'));
let SnapshotRefresh;
try {
 const file=path.join(dir,'refresh.mjs');
 await build({entryPoints:['renderer/refresh.ts'],outfile:file,bundle:true,platform:'node',format:'esm'});
 ({SnapshotRefresh}=await import(pathToFileURL(file)));
} finally {await rm(dir,{recursive:true,force:true});}
test('simultaneous refreshes share a read; newer terminal event requires a fresh snapshot',async()=>{
 const loader=new SnapshotRefresh();let calls=0,release;
 const read=async()=>{calls++;if(calls===1)await new Promise(resolve=>release=resolve);return{sequence:calls===1?4:8};};
 const first=loader.run('t',4,read);await Promise.resolve();
 const same=loader.run('t',4,read);assert.equal(first,same);assert.equal(calls,1);
 const newer=loader.run('t',8,read);assert.equal(first,newer);release();
 assert.equal((await newer).sequence,8);assert.equal(calls,2);
 await loader.run('t',8,read);assert.equal(calls,3);
});
test('read errors release pending entry and different sessions do not share requests',async()=>{
 const loader=new SnapshotRefresh();
 await assert.rejects(loader.run('a',0,async()=>{throw Error('offline')}),/offline/);
 assert.equal((await loader.run('a',0,async()=>({sequence:1}))).sequence,1);
 const results=await Promise.all(['a','b'].map(key=>loader.run(key,0,async()=>({sequence:1,key}))));
 assert.deepEqual(results.map(r=>r.key),['a','b']);
});
test('a restarted host with reset sequence cannot cause an endless refresh loop',async()=>{
 const loader=new SnapshotRefresh();let calls=0;
 const result=await loader.run('old:t',100,async()=>{calls++;return{sequence:0,instance:'new'}},'old');
 assert.equal(result.instance,'new');assert.equal(calls,1);
});
