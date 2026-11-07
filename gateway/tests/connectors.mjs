import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, writeFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { EventEmitter } from "node:events";
import { create as telegram } from "../connectors/telegram.mjs";
import { create as feishu } from "../connectors/feishu.mjs";
import { create as wechat, trustedBase } from "../connectors/wechat.mjs";
import { create as whatsapp } from "../connectors/whatsapp.mjs";
import { chunks } from "../connectors/protocol.mjs";
import {memoryAuth} from './direct-fixtures.mjs';

const reply = (value) => ({ ok: true, status: 200, json: async () => value });
test("Telegram marks unsupported private media, filters groups and preserves text replies", async () => {
  const original = globalThis.fetch,
    calls = [];
  let adapter,
    received = [];
  globalThis.fetch = async (url, request) => {
    const method = url.split("/").at(-1),
      body = JSON.parse(request.body);
    calls.push({ method, body });
    if (method === "getMe") return reply({ ok: true, result: { id: 42 } });
    if (method === "getWebhookInfo")
      return reply({ ok: true, result: { url: "" } });
    if (method === "getUpdates")
      return reply({
        ok: true,
        result: [
          {
            update_id: 9,
            message: {
              chat: { type: "group", id: 2 },
              from: { id: 1 },
              text: "ignore",
            },
          },
          {
            update_id: 10,
            message: {chat: {type: "private", id: 2}, from: {id: 1},
                      voice: {file_id: "fixture-voice"}, caption: "A voice"},
          },
          {
            update_id: 11,
            message: {
              chat: { type: "private", id: 2 },
              from: { id: 1 },
              text: "hello",
            },
          },
        ],
      });
    return reply({ ok: true, result: { message_id: 12 } });
  };
  try {
    adapter = await telegram({
      config: { token: "fixture" },
      cursor: "8",
      incoming: async (m) => {
        received.push(m);
        if(m.unsupported)return {accepted:true};
        await adapter.send({ message: m, text: "字".repeat(3200) });
        await adapter.close();
        return { accepted: true };
      },
    });
    await adapter.start();
    assert.equal(received.length, 2);
    assert.equal(received[0].unsupported,true);
    assert.equal(received[0].text,"A voice\n[语音消息]");
    assert.equal(received[1].id, "11");
    assert.equal(received[1].unsupported,undefined);
    assert.equal(calls.find((c) => c.method === "getUpdates").body.offset, 8);
    assert.equal(calls.filter((c) => c.method === "sendMessage").length, 2);
  } finally {
    globalThis.fetch = original;
    await adapter?.close();
  }
});
test("Feishu lifecycle callbacks, filtering and send request match SDK contract", async () => {
  let opts,
    terminal = false,
    dispatch,
    closed = false,
    sent,
    received = [];
  class WS {
    constructor(o) {
      opts = o;
    }
    async start(p) {
      dispatch = p.eventDispatcher.events;
    }
    close(p) {
      closed = p.force;
    }
  }
  class Dispatcher {
    register(events) {
      this.events = events;
      return this;
    }
  }
  class Client {
    im = {
      message: {
        create: async (value) => {
          sent = value;
          return { code: 0 };
        },
      },
    };
  }
  const sdk = {
    Domain: { Lark: "lark", Feishu: "feishu" },
    LoggerLevel: { error: 0 },
    WSClient: WS,
    EventDispatcher: Dispatcher,
    Client,
  };
  const adapter = await feishu({
    config: {
      app_id: "cli_0123456789abcdef",
      app_secret: "fixture",
      region: "Lark",
    },
    require: () => sdk,
    fatal: () => { terminal = true; },
    incoming: async (m) => received.push(m),
  });
  await adapter.start();
  assert.equal(opts.domain, "lark");
  assert.equal(typeof opts.onReady, "function");
  opts.onError(new Error("temporary failure"));
  assert.equal(terminal, false);
  opts.onError(new Error("WebSocket reconnect exhausted after 3 attempts"));
  assert.equal(terminal, true);
  const data = {
    message: {
      chat_type: "p2p",
      message_type: "text",
      content: '{"text":"hello"}',
      chat_id: "room",
      message_id: "one",
    },
    sender: { sender_type: "user", sender_id: { open_id: "person" } },
  };
  await dispatch["im.message.receive_v1"](data);
  await dispatch["im.message.receive_v1"]({...data,message:{...data.message,content:'{broken'}});
  await dispatch["im.message.receive_v1"]({...data,sender:{sender_type:'user'}});
  await dispatch["im.message.receive_v1"]({
    ...data,
    message: { ...data.message, chat_type: "group" },
  });
  assert.equal(received.length, 1);
  await dispatch["im.message.receive_v1"]({...data,message:{...data.message,
    message_id:"video",message_type:"media",content:'{}'}});
  assert.equal(received.length,2);
  assert.equal(received[1].unsupported,true);
  await adapter.send({ message: received[0], text: "answer" });
  assert.equal(sent.params.receive_id_type, "chat_id");
  assert.equal(sent.data.receive_id, "room");
  await adapter.close();
  assert(closed);
});
test("WeChat login, durable credentials, context-token reply and trusted redirect boundary", async () => {
  assert.throws(() => trustedBase("https://example.invalid/"));
  assert.throws(() => trustedBase("http://ilinkai.weixin.qq.com"));
  assert.throws(() =>
    trustedBase("https://ilinkai.weixin.qq.com@evil.invalid"),
  );
  const directory = await mkdtemp(
    path.join(os.tmpdir(), "corki-wechat-fixture-"),
  );
  const original = globalThis.fetch;
  let adapter,
    sent,
    seen = [];
  globalThis.fetch = async (url, request) => {
    if (url.includes("get_bot_qrcode"))
      return reply({
        qrcode: "fixture-qr",
        qrcode_img_content: "fixture-login",
      });
    if (url.includes("get_qrcode_status"))
      return reply({
        status: "confirmed",
        bot_token: "fixture-login-token",
        ilink_bot_id: "fixture-bot",
        baseurl: "https://ilinkai.weixin.qq.com",
      });
    if (url.includes("getupdates"))
      return reply({
        msgs: [
          {message_id: "fixture-voice", message_type:1, from_user_id:"peer",
           context_token:"fixture-context",item_list:[{type:3}]},
          {message_id:"fixture-image",message_type:1,from_user_id:"peer",
           context_token:"fixture-context",item_list:[
             {type:1,text_item:{text:"看看😀"}},
             {type:2,image_item:{media:{encrypt_query_param:"private-image",aes_key:"private-key"}}},
             {type:1,text_item:{text:"后文"}}
           ]},
          {message_id:'fixture-files',message_type:1,from_user_id:'peer',item_list:[
            {type:4,file_item:{file_name:'说明.txt',media:{encrypt_query_param:'file'}}},
            {type:5,video_item:{media:{encrypt_query_param:'video'}}}
          ]},
          {
            message_id: "fixture-id",
            message_type: 1,
            from_user_id: "peer",
            context_token: "fixture-context",
            item_list: [{ type: 1, text_item: { text: "hello" } }],
          },
        ],
        get_updates_buf: "next",
      });
    if (url.includes("sendmessage")) {
      sent = JSON.parse(request.body);
      return reply({ ret: 0 });
    }
    throw Error("unexpected request");
  };
  try {
    adapter = await wechat({
      state_directory: directory,
      cursor: "",
      require: () => ({
        toDataURL: async () => "data:image/png;base64,fixture",
      }),
      incoming: async (m) => {
        seen.push(m);
        if(m.unsupported)return {accepted:true};
        if(m.image_refs?.length)return {accepted:true};
        if(m.file_refs?.length)return {accepted:true};
        await adapter.send({ message: m, text: "reply" });
        await adapter.close();
        return { accepted: true };
      },
    });
    const started = adapter.start();
    await adapter.login();
    await started;
    assert.equal(seen.length, 4);
    assert.equal(seen[0].unsupported,true);
    assert.equal(seen[1].unsupported,undefined);
    assert.equal(seen[1].image_refs.length,1);
    const position=seen[1].image_positions[0];
    assert.equal(Array.from(seen[1].text).slice(position,position+10).join(''),'[Image #1]');
    assert(seen[1].text.startsWith('看看😀'));
    assert.equal(seen[2].file_refs.length,2);
    assert.deepEqual(seen[2].file_positions,[0,10]);
    assert.equal(sent.msg.context_token, "fixture-context");
    assert.equal(sent.msg.message_state, 2);
    assert.equal(
      JSON.parse(await readFile(path.join(directory, "login.json"), "utf8")).id,
      "fixture-bot",
    );
  } finally {
    globalThis.fetch = original;
    await adapter?.close();
    await rm(directory, { recursive: true, force: true });
  }
});
test("WeChat ID-only sends omit context and platform rejection is never reported as success", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "corki-wechat-send-"));
  const original = globalThis.fetch;
  let adapter, body, mode = "success";
  try {
    await writeFile(path.join(directory,"login.json"), JSON.stringify({
      id:"fixture-bot",token:"private-bot-token",base:"https://ilinkai.weixin.qq.com"
    }));
    globalThis.fetch = async (_url,request) => {
      body=JSON.parse(request.body);
      if(mode==='http')return new Response('private-bot-token private-context',{status:403});
      if(mode==='invalid-json')return new Response('not JSON');
      if(mode==='code-only-success')return reply({errcode:0});
      if(mode==='malformed')return reply(null);
      if(mode==='array')return reply([]);
      if(mode==='invalid-code')return reply({ret:'invalid'});
      if(mode==='errcode')return reply({ret:0,errcode:-7});
      return reply(mode==='reject'?{ret:-7,errmsg:'private-bot-token private-context'}:mode==='empty-success'?{}:{ret:0});
    };
    adapter=await wechat({state_directory:directory,cursor:'',incoming:async()=>({accepted:true})});
    await adapter.send({message:{conversation:'fixture-peer'},text:'ID-only'});
    assert.equal(body.msg.to_user_id,'fixture-peer');
    assert.equal(Object.hasOwn(body.msg,'context_token'),false);
    await adapter.send({message:{conversation:'fixture-peer',context_token:'private-context'},text:'Reply'});
    assert.equal(body.msg.context_token,'private-context');
    mode='empty-success';
    await adapter.send({message:{conversation:'fixture-peer'},text:'Tencent omits zero codes'});
    mode='code-only-success';
    await adapter.send({message:{conversation:'fixture-peer'},text:'Alternate success field'});
    for(mode of ['reject','http','malformed','array','invalid-code','invalid-json','errcode']) {
      await assert.rejects(adapter.send({message:{conversation:'fixture-peer',context_token:'private-context'},text:'Rejected'}),error=>{
        assert.equal(error.delivery.provider,'wechat');
        assert(!JSON.stringify(error.delivery).includes('private-'));
        return true;
      });
    }
  } finally {
    globalThis.fetch=original;
    await adapter?.close();
    await rm(directory,{recursive:true,force:true});
  }
});

test("WhatsApp ignores own, group and history messages and does not log out on stop", async (t) => {
  const ev = new EventEmitter();
  let sent,
    ended = false;
  const received = [];
  const socket = {
    ev,
    user: { id: "12345:1@s.whatsapp.net" },
    sendMessage: async (...args) => {
      sent = args;
      return {key:{id:'sent'}};
    },
    end: () => (ended = true),
  };
  const api = {
    default: () => socket,
    useMultiFileAuthState: async () => ({ state: {}, saveCreds: () => {} }),
    normalizeMessageContent: (m) => m,
    DisconnectReason: { loggedOut: 401 },
  };
  const adapter = await whatsapp({
    authFactory:memoryAuth,
    state_directory: "/unused-fixture",
    require: (name) =>
      name === "pino" ? () => ({}) : { toDataURL: async () => "" },
    load: async () => api,
    incoming: async (m) => {received.push(m);return {accepted:true};},
  });
  t.after(()=>adapter.close());
  await adapter.start();
  ev.emit('connection.update',{connection:'open'});
  await new Promise(resolve=>setImmediate(resolve));
  const item = {
    key: { id: "one", remoteJid: "22222@s.whatsapp.net" },
    message: { conversation: "hello" },
  };
  ev.emit("messages.upsert", { type: "append", messages: [item] });
  ev.emit("messages.upsert", {
    type: "notify",
    messages: [
      { ...item, key: { ...item.key, fromMe: true } },
      { ...item, key: { ...item.key, remoteJid: "group@g.us" } },
      item,
      {...item,key:{...item.key,id:'audio'},message:{audioMessage:{}}},
    ],
  });
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(received.length, 2);
  assert.equal(received[1].unsupported,true);
  await adapter.send({ message: received[0], text: "answer" });
  assert.deepEqual(sent, ["22222@s.whatsapp.net", { text: "answer" },{mediaUploadTimeoutMs:45000}]);
  await adapter.close();
  assert(ended);
  assert.equal(chunks("🦀".repeat(10), 3).join(""), "🦀".repeat(10));
  assert(chunks("🦀".repeat(4000)).every((part) => part.length <= 3000));
});

test("DingTalk admission ACK, private text, safe replies, token reuse and stop", async () => {
  const { create, replyURL } = await import("../connectors/dingtalk.mjs");
  const original = globalThis.fetch, admitted = [], ack = [], requests = [];
  let callback, options, disconnected = false;
  class Client {
    constructor(value) { options = value; }
    registerCallbackListener(topic, handler) { assert.equal(topic, "robot"); callback = handler; }
    async connect() { this.connected = true; }
    socketCallBackResponse(id, result) { ack.push([id, result]); }
    disconnect() { disconnected = true; this.connected = false; }
  }
  let failAdmission = false;
  const adapter = await create({
    config: { client_id: "fixture-app", client_secret: "fixture-secret" },
    require: name => { assert.equal(name, "dingtalk-stream"); return { DWClient: Client, TOPIC_ROBOT: "robot" }; },
    incoming: async value => { if (failAdmission) throw new Error("busy"); admitted.push(value); },
  });
  const message = {
    msgId:"fixture", senderId:"person", senderStaffId:"fixture-staff", robotCode:"fixture-robot", conversationId:"dm", conversationType:"1",
    msgtype:"text", text:{content:"Hello"}, sessionWebhook:"https://oapi.dingtalk.com/robot/sendBySession?session=fixture",
    sessionWebhookExpiredTime:Date.now()+3600000,
  };
  const frame = data => ({headers:{messageId:"frame-id"},data:JSON.stringify(data)});
  try {
    await adapter.start();
    assert.equal(options.autoReconnect,false);
    assert.equal(options.keepAlive,true);
    assert.deepEqual(options.subscriptions,[]);
    await callback(frame({...message,conversationType:"2"}));
    await callback(frame({...message,msgtype:""}));
    assert.equal(admitted.length,0);
    await callback(frame(message));
    assert.equal(admitted.length,1);
    assert.equal(admitted[0].staff_id,"fixture-staff");
    assert.equal(admitted[0].robot_code,"fixture-robot");
    assert.equal(ack.length,3);
    await callback(frame({...message,msgId:'video-fixture',msgtype:"video"}));
    assert.equal(admitted.length,2);
    assert.equal(admitted[1].unsupported,true);
    assert.equal(admitted[1].text,'[视频消息]');
    assert.equal(ack.length,4);
    failAdmission = true;
    await callback(frame(message));
    assert.equal(ack.length,4); // Failed durable admission must not be acknowledged.
    for(const bad of ["http://oapi.dingtalk.com/robot/sendBySession","https://example.invalid/",
      "https://oapi.dingtalk.com.evil.invalid/robot/sendBySession",
      "https://oapi.dingtalk.com/other","https://user@oapi.dingtalk.com/robot/sendBySession"])
      assert.throws(()=>replyURL(bad));
    globalThis.fetch = async (url, request) => {
      requests.push([url,request]);
      assert.equal(request.redirect,"error");
      return reply(url.includes("/oauth2/") ? {accessToken:"fixture-access",expireIn:7200} : {errcode:0});
    };
    await adapter.send({message:admitted[0],text:"你".repeat(2001)});
    await adapter.send({message:admitted[0],text:"again"});
    assert.equal(requests.filter(([url])=>url.includes("/oauth2/")).length,1);
    assert.equal(requests.length,5);
    assert.equal(requests[1][1].headers["x-acs-dingtalk-access-token"],"fixture-access");
    await assert.rejects(adapter.send({message:{...admitted[0],webhook_expires:1},text:"expired"}));
    globalThis.fetch = async () => reply({errcode:1});
    await assert.rejects(adapter.send({message:admitted[0],text:"failed"}));
  } finally {
    await adapter.close();
    globalThis.fetch = original;
  }
  assert.ok(disconnected);
});

test("DingTalk SDK swallowed connection failures are not marked online", async () => {
  const { create } = await import("../connectors/dingtalk.mjs");
  class FailedClient {
    connected = false;
    registerCallbackListener() {}
    async connect() {}
    disconnect() {}
  }
  const adapter = await create({config:{},require:()=>({DWClient:FailedClient,TOPIC_ROBOT:"robot"}),incoming:async()=>{}});
  try { await assert.rejects(adapter.start(), /not connected/); }
  finally { await adapter.close(); }
});

test("DingTalk captures HTTP details before SDK swallows failure", async () => {
  const {create}=await import('../connectors/dingtalk.mjs');
  class Client {
    config={};connected=false;
    registerCallbackListener(){}
    async getEndpoint(){const error=new Error('Request failed');error.response={status:403,data:'blocked fixture-secret'};throw error;}
    async connect(){try{await this.getEndpoint();}catch{}}
    disconnect(){}
  }
  const adapter=await create({config:{client_id:'fixture-app',client_secret:'fixture-secret'},
    require:()=>({DWClient:Client,TOPIC_ROBOT:'robot'}),incoming:async()=>{}});
  const write=process.stdout.write, events=[];
  process.stdout.write=function(chunk,...args){
    try{events.push(JSON.parse(String(chunk)));}catch{}
    return write.call(this,chunk,...args);
  };
  try{
    await assert.rejects(adapter.start(),/not connected/);
    const detail=events.find(e=>e.params?.detail)?.params.detail;
    assert.equal(detail.status,403);
    assert.equal(detail.url,'https://api.dingtalk.com/v1.0/gateway/connections/open');
    assert.equal(detail.response,'blocked [已隐藏]');
    assert.equal(detail.stage,'获取钉钉连接地址');
  }
  finally{process.stdout.write=write;await adapter.close();}
});
