import test from 'node:test';
import assert from 'node:assert/strict';
import { create } from '../connectors/feishu.mjs';

for (const kind of ['http', 'network', 'api']) {
  test(`Feishu preserves ${kind} discovery diagnostics before SDK swallows errors`, async () => {
    const events = [], write = process.stdout.write;
    let adapter, options;
    process.stdout.write = function(chunk, ...args) {
      try { events.push(JSON.parse(String(chunk))); } catch {}
      return write.call(this, chunk, ...args);
    };
    const sdk = {
      Domain: {Feishu:'feishu', Lark:'lark'}, LoggerLevel:{error:0},
      defaultHttpInstance: {async request(request) {
        assert.equal(request.url, 'https://open.feishu.cn/callback/ws/endpoint');
        if(kind === 'api') return {code:100004, msg:'invalid fixture-secret', access_token:'private-token'};
        const error = new Error(kind === 'http' ? 'HTTP failure' : 'ECONNREFUSED');
        if(kind === 'http') error.response = {status:403, data:'blocked fixture-secret', headers:{'content-type':'text/plain'}};
        throw error;
      }},
      Client: class {},
      EventDispatcher: class {register() {return this;}},
      WSClient: class {
        constructor(value) {options=value;}
        async start() {
          // The SDK catches HTTP failures instead of forwarding the response.
          try { await options.httpInstance.request({url:'https://open.feishu.cn/callback/ws/endpoint',method:'post'}); } catch {}
          options.onError(new Error('WebSocket reconnect exhausted after 3 attempts'));
        }
        close() {}
      },
    };
    try {
      adapter=await create({config:{app_id:'cli_0123456789abcdef', app_secret:'fixture-secret'}, require:()=>sdk});
      await adapter.start();
      const failures=events.filter(e=>e.method==='status' && e.params.state==='error');
      assert.equal(failures.length,1, 'generic SDK error must not replace the HTTP detail');
      const detail=failures[0].params.detail;
      assert.equal(detail.stage,'获取飞书连接地址');
      assert.equal(detail.method,'POST');
      assert.equal(detail.url,'https://open.feishu.cn/callback/ws/endpoint');
      if(kind==='http') {assert.equal(detail.status,403);assert.equal(detail.response,'blocked [已隐藏]');}
      if(kind==='network') {assert.equal(detail.error,'ECONNREFUSED');assert.equal(detail.status,undefined);}
      const serialized=JSON.stringify(detail);
      assert(!serialized.includes('fixture-secret'));
      assert(!serialized.includes('private-token'));
      options.onReady();
      options.onError(new Error('new websocket failure'));
      assert.equal(events.at(-1).params.detail.stage,'建立飞书 WebSocket');
    } finally {await adapter?.close();process.stdout.write=write;}
  });
}

test('Feishu invalid app ID reports configuration failure before initialization exits', async () => {
  const events=[], write=process.stdout.write;
  process.stdout.write=function(chunk,...args) {
    try {events.push(JSON.parse(String(chunk)));} catch {}
    return write.call(this,chunk,...args);
  };
  try {
    await assert.rejects(create({config:{app_id:'invalid',app_secret:'fixture-secret'},require:()=>({Domain:{Feishu:'feishu'},LoggerLevel:{error:0}})}),/invalid app id/);
    assert.equal(events.at(-1).params.detail.stage,'校验飞书接入配置');
    assert.match(events.at(-1).params.detail.error,/App ID 格式无效/);
    assert(!JSON.stringify(events).includes('fixture-secret'));
  } finally {process.stdout.write=write;}
});
