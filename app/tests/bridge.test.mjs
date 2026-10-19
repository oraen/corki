import test from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
const directory = await mkdtemp(path.join(os.tmpdir(), "corki-node-bridge-"));
await build({
  entryPoints: ["electron/bridge.ts"],
  outfile: path.join(directory, "bridge.mjs"),
  bundle: true,
  platform: "node",
  format: "esm",
});
const { Bridge } = await import(
  pathToFileURL(path.join(directory, "bridge.mjs"))
);
const python = path.resolve("../.venv/bin/python");
const fixture = path.join(directory, "fixture.py");
await writeFile(
  fixture,
  `
import json, sys, os, time
for raw in sys.stdin.buffer:
    req=json.loads(raw)
    if req['method']=='initialize':
        result={'protocolVersion':1}
    elif req['params'].get('malformed'):
        print(json.dumps({'jsonrpc':'2.0','method':'event','params':{'type':'invalid'}}),flush=True)
        continue
    elif req['params'].get('exit'):
        sys.exit(0)
    else:
        result={'text':'中文回复'}
    frame=(json.dumps({'jsonrpc':'2.0','id':req['id'],'result':result},ensure_ascii=False)+'\\n').encode()
    split=frame.find('中'.encode())+1
    if split>0:
        os.write(1,frame[:split]);time.sleep(.01);os.write(1,frame[split:])
    else:
        os.write(1,frame)
`,
);
test.after(async () => {
  await rm(directory, { recursive: true, force: true });
});
test("native bridge decodes split UTF-8 and shuts down on stdin EOF", async () => {
  const bridge = new Bridge(python, fixture, directory, directory);
  try {
    assert.deepEqual(await bridge.request("status"), { text: "中文回复" });
  } finally {
    await bridge.close();
  }
});
test("malformed event envelope rejects pending requests instead of reaching renderer", async () => {
  const bridge = new Bridge(python, fixture, directory, directory);
  const events = [];
  bridge.on("event", (event) => events.push(event));
  try {
    await assert.rejects(
      bridge.request("status", { malformed: true }),
      /服务协议异常/,
    );
    assert.equal(events.length, 0);
  } finally {
    await bridge.close();
  }
});
test("unexpected backend exit rejects a pending request without retrying", async () => {
  const bridge = new Bridge(python, fixture, directory, directory);
  try {
    await assert.rejects(bridge.request("status", { exit: true }), /断开/);
  } finally {
    await bridge.close();
  }
});

test("actual Python transport fragments large responses and events with concurrent requests", async () => {
  const largeFixture=path.join(directory,'large.py');
  await writeFile(largeFixture, `
import asyncio,sys
sys.path.insert(0,${JSON.stringify(path.resolve('bridge'))})
from protocol import Transport
class Service:
    stopping=False
    async def dispatch(self,method,params):
        if method=='initialize': return {'protocolVersion':1}
        value='中文🦀'*400000
        await transport.send({'jsonrpc':'2.0','method':'event','params':{
            'instance':'fixture','sequence':1,'type':'large','threadId':'fixture','data':{'text':value}}})
        return {'text':value,'request':params['which']}
    def safe_error(self,error): return str(error)
    async def close(self): pass
async def main():
    global transport
    transport=Transport()
    await transport.serve(Service())
asyncio.run(main())
`);
  const bridge=new Bridge(python,largeFixture,directory,directory);
  const events=[];bridge.on('event',event=>events.push(event));
  try {
    const results=await Promise.all([bridge.request('status',{which:1}),bridge.request('status',{which:2})]);
    const expected='中文🦀'.repeat(400000);
    assert.deepEqual(results,[{text:expected,request:1},{text:expected,request:2}]);
    assert.equal(events.length,2);assert(events.every(event=>event.data.text===expected));
  } finally {await bridge.close()}
});
