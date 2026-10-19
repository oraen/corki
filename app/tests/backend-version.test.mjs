import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {mkdtemp,rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const dir=await mkdtemp(path.join(os.tmpdir(),'corki-version-'));
let check;
try {
  const file=path.join(dir,'protocol.mjs');
  await build({entryPoints:['shared/protocol.ts'],outfile:file,bundle:true,platform:'node',format:'esm'});
  check=(await import(pathToFileURL(file))).assertThreadManagement;
} finally {await rm(dir,{recursive:true,force:true});}
test('old archive-only backend is rejected before deletion or reminder access',()=>{
  assert.throws(()=>check({protocolVersion:1}),/完全退出 Corki/);
  assert.throws(()=>check({threadManagementVersion:1}),/仅刷新页面不会更新后台/);
  assert.doesNotThrow(()=>check({threadManagementVersion:2}));
});
