import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { EventEmitter } from "node:events";
import { create as telegram } from "../connectors/telegram.mjs";
import { create as feishu } from "../connectors/feishu.mjs";
import { create as wechat, trustedBase } from "../connectors/wechat.mjs";
import { create as whatsapp } from "../connectors/whatsapp.mjs";
import { chunks } from "../connectors/protocol.mjs";

const reply = (value) => ({ ok: true, status: 200, json: async () => value });
test("Telegram admits only direct human text, persists cursor after admission and splits replies", async () => {
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
        await adapter.send({ message: m, text: "字".repeat(3200) });
        await adapter.close();
        return { accepted: true };
      },
    });
    await adapter.start();
    assert.equal(received.length, 1);
    assert.equal(received[0].id, "10");
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
  await dispatch["im.message.receive_v1"]({
    ...data,
    message: { ...data.message, chat_type: "group" },
  });
  assert.equal(received.length, 1);
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
        await adapter.send({ message: m, text: "reply" });
        await adapter.close();
        return { accepted: true };
      },
    });
    await adapter.start();
    assert.equal(seen.length, 1);
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
test("WhatsApp ignores own, group and history messages and does not log out on stop", async () => {
  const ev = new EventEmitter();
  let sent,
    ended = false;
  const received = [];
  const socket = {
    ev,
    user: { id: "fixture-account" },
    sendMessage: async (...args) => {
      sent = args;
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
    state_directory: "/unused-fixture",
    require: (name) =>
      name === "pino" ? () => ({}) : { toDataURL: async () => "" },
    load: async () => api,
    incoming: async (m) => received.push(m),
  });
  await adapter.start();
  const item = {
    key: { id: "one", remoteJid: "peer@s.whatsapp.net" },
    message: { conversation: "hello" },
  };
  ev.emit("messages.upsert", { type: "append", messages: [item] });
  ev.emit("messages.upsert", {
    type: "notify",
    messages: [
      { ...item, key: { ...item.key, fromMe: true } },
      { ...item, key: { ...item.key, remoteJid: "group@g.us" } },
      item,
    ],
  });
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(received.length, 1);
  await adapter.send({ message: received[0], text: "answer" });
  assert.deepEqual(sent, ["peer@s.whatsapp.net", { text: "answer" }]);
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
    msgId:"fixture", senderId:"person", conversationId:"dm", conversationType:"1",
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
    await callback(frame({...message,msgtype:"image"}));
    assert.equal(admitted.length,0);
    await callback(frame(message));
    assert.equal(admitted.length,1);
    assert.equal(ack.length,3);
    failAdmission = true;
    await callback(frame(message));
    assert.equal(ack.length,3); // Failed durable admission must not be acknowledged.
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
