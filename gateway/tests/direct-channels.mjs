import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, readFile, rm } from "node:fs/promises";
import { createHash } from "node:crypto";
import { EventEmitter } from "node:events";
import { Readable } from "node:stream";
import path from "node:path";
import os from "node:os";
import { create as telegram } from "../connectors/telegram.mjs";
import { create as whatsapp } from "../connectors/whatsapp.mjs";
import { createAuth } from "../connectors/whatsapp-auth.mjs";
import { memoryAuth } from "./direct-fixtures.mjs";

const tick = () => new Promise((resolve) => setImmediate(resolve));
const reply = (result) => new Response(JSON.stringify({ ok: true, result }));
const video = await readFile(
  new URL("./fixtures/wechat-video.mp4", import.meta.url),
);
const image = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1ioAAAAASUVORK5CYII=",
  "base64",
);

test("Telegram private image/video/file round trip, cursor ACK, failures and typing", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "corki-tg-")),
    original = globalThis.fetch;
  const received = [],
    sent = [],
    blobs = { photo: image, video, document: Buffer.from("你好文件") };
  let adapter,
    polled = false,
    ready,
    mode = "ok",
    sendCalls = 0;
  const admitted = new Promise((resolve) => (ready = resolve));
  globalThis.fetch = async (url, request) => {
    if (url.includes("/file/bot"))
      return new Response(blobs[url.split("/").at(-1)]);
    const method = url.split("/").at(-1),
      body =
        request.body instanceof FormData
          ? request.body
          : JSON.parse(request.body);
    if (method === "getMe") return reply({ id: 42 });
    if (method === "getWebhookInfo") return reply({ url: "" });
    if (method === "getFile")
      return reply({ file_path: "fixtures/" + body.file_id });
    if (method === "sendChatAction") return reply(true);
    if (method === "getUpdates") {
      if (polled)
        return new Promise((_, reject) =>
          request.signal.addEventListener(
            "abort",
            () => reject(new Error("stop")),
            { once: true },
          ),
        );
      polled = true;
      return reply(
        ["photo", "video", "document"].map((kind, index) => ({
          update_id: index + 1,
          message: {
            message_id: index + 10,
            chat: { id: 123, type: "private" },
            from: { id: 123, first_name: "Alice" },
            caption: "Question " + index,
            [kind]:
              kind === "photo"
                ? [{ file_id: kind }]
                : {
                    file_id: kind,
                    file_name: kind === "video" ? "演示.mp4" : "说明.txt",
                  },
          },
        })),
      );
    }
    sendCalls++;
    if (mode === "blocked")
      return new Response(
        JSON.stringify({
          ok: false,
          error_code: 403,
          description: "private-token private-recipient",
        }),
        { status: 403 },
      );
    if (mode === "limited")
      return new Response(
        JSON.stringify({
          ok: false,
          error_code: 429,
          parameters: { retry_after: 60 },
        }),
        { status: 429 },
      );
    if (mode === "unknown") return reply({});
    sent.push({ method, body });
    return reply({ message_id: 100 + sent.length });
  };
  try {
    adapter = await telegram({
      state_directory: directory,
      config: { token: "private-token" },
      incoming: async (m) => {
        received.push(m);
        if (received.length === 3) ready();
        return { accepted: true };
      },
    });
    const started = adapter.start();
    await admitted;
    assert.equal(received.length, 3);
    assert.deepEqual(received[0].image_positions, [11]);
    for (const m of received) {
      const ref = (m.image_refs || m.file_refs)[0],
        root = m.image_refs ? "images" : "files";
      assert(
        (await readFile(path.join(directory, root, ref.id + ".bin"))).length >
          0,
      );
      assert(!JSON.stringify(m).includes("private-token"));
    }
    await adapter.send({
      message: received[0],
      text: "reply",
      images: [{ id: received[0].image_refs[0].id, mime: "image/png" }],
      files: [{ ...received[1].file_refs[0] }, { ...received[2].file_refs[0] }],
    });
    assert.deepEqual(
      sent.map((x) => x.method),
      ["sendMessage", "sendPhoto", "sendVideo", "sendDocument"],
    );
    assert.deepEqual(
      Buffer.from(await sent[1].body.get("photo").arrayBuffer()),
      image,
    );
    assert.equal(
      (await adapter.typing({ message: received[0], active: true })).supported,
      true,
    );
    for (mode of ["blocked", "limited", "unknown"]) {
      const before = sendCalls;
      await assert.rejects(
        adapter.send({ message: received[0], text: "reject" }),
        (e) => {
          assert.equal(e.delivery.provider, "telegram");
          assert(!JSON.stringify(e.delivery).includes("private-"));
          return true;
        },
      );
      assert.equal(sendCalls, before + 1);
    }
    await adapter.close();
    await started;
  } finally {
    await adapter?.close();
    globalThis.fetch = original;
    await rm(directory, { recursive: true, force: true });
  }
});

test("Telegram refuses an existing webhook without deleting it", async () => {
  const original = globalThis.fetch,
    methods = [];
  globalThis.fetch = async (url) => {
    const method = url.split("/").at(-1);
    methods.push(method);
    return reply(
      method === "getMe"
        ? { id: 42 }
        : { url: "https://private.invalid/webhook" },
    );
  };
  const adapter = await telegram({
    config: { token: "private" },
    incoming: async () => {},
  });
  try {
    await assert.rejects(adapter.start(), (e) => e.reported === true);
    assert.deepEqual(methods, ["getMe", "getWebhookInfo"]);
  } finally {
    await adapter.close();
    globalThis.fetch = original;
  }
});

test("WhatsApp login on demand, stale QR callbacks, media, aliases and confirmed sends", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "corki-wa-")),
    auth = await memoryAuth();
  auth.state.creds.registered = false;
  const sockets = [],
    received = [],
    sent = [],
    presence = [];
  let fail = false;
  const api = {
    DisconnectReason: {
      loggedOut: 401,
      badSession: 500,
      restartRequired: 515,
      connectionReplaced: 440,
    },
    normalizeMessageContent: (m) => m,
    default: () => {
      const socket = {
        ev: new EventEmitter(),
        user: { id: "11111:2@s.whatsapp.net" },
        end() {},
        signalRepository: {
          lidMapping: { getPNForLID: async () => "22222@s.whatsapp.net" },
        },
        sendPresenceUpdate: async (...args) => presence.push(args),
        sendMessage: async (...args) => {
          sent.push(args);
          if (fail) throw new Error("private-socket-secret");
          return { key: { id: "sent-" + sent.length } };
        },
      };
      sockets.push(socket);
      return socket;
    },
    downloadMediaMessage: async (item) =>
      Readable.from([item.message.imageMessage ? image : video]),
  };
  const adapter = await whatsapp({
    state_directory: directory,
    authFactory: async () => auth,
    load: async () => api,
    require: (name) =>
      name === "pino"
        ? () => ({})
        : { toDataURL: async (qr) => "data:image/png;base64," + qr },
    incoming: async (m) => {
      received.push(m);
      return { accepted: true };
    },
  });
  try {
    await adapter.start();
    assert.equal(sockets.length, 0);
    await adapter.login();
    assert.equal(sockets.length, 1);
    await adapter.login({ refresh: true });
    assert.equal(sockets.length, 2);
    sockets[0].ev.emit("connection.update", { connection: "open" });
    await tick();
    const socket = sockets[1];
    auth.state.creds.registered = true;
    socket.ev.emit("connection.update", { connection: "open" });
    await tick();
    socket.ev.emit("messages.upsert", {
      type: "append",
      messages: [
        {
          key: { id: "old", remoteJid: "22222@s.whatsapp.net" },
          message: { conversation: "old" },
        },
      ],
    });
    socket.ev.emit("messages.upsert", {
      type: "notify",
      messages: [
        {
          key: { id: "photo", remoteJid: "22222@s.whatsapp.net" },
          message: { imageMessage: { caption: "Question A" } },
          pushName: "Alice",
        },
        {
          key: {
            id: "video",
            remoteJid: "33333@lid",
            remoteJidAlt: "22222@s.whatsapp.net",
          },
          message: { videoMessage: { caption: "Question B" } },
          pushName: "Alice",
        },
        {
          key: { id: "group", remoteJid: "44444@g.us" },
          message: { conversation: "ignore" },
        },
      ],
    });
    for (let i = 0; i < 100 && received.length < 2; i++)
      await new Promise((r) => setTimeout(r, 5));
    assert.equal(received.length, 2);
    assert.equal(received[0].sender, received[1].sender);
    assert.equal(received[0].image_positions[0], 11);
    socket.ev.emit("messages.upsert", {
      type: "notify",
      messages: [
        {
          key: { id: "photo", remoteJid: "44444@s.whatsapp.net" },
          message: { conversation: "same ID, another user" },
        },
      ],
    });
    for (let i = 0; i < 30 && received.length < 3; i++) await tick();
    assert.equal(received.length, 3);
    assert.notEqual(received[0].id, received[2].id);
    await adapter.send({
      message: received[0],
      text: "answer",
      images: [{ id: received[0].image_refs[0].id, mime: "image/png" }],
      files: [received[1].file_refs[0]],
    });
    assert.equal(sent.length, 3);
    assert(Buffer.isBuffer(sent[1][1].image));
    assert(Buffer.isBuffer(sent[2][1].video));
    assert(Buffer.isBuffer(sent[2][1].jpegThumbnail));
    assert.equal(sent[2][2].mediaUploadTimeoutMs, 45000);
    await adapter.typing({ message: received[0], active: true });
    await adapter.typing({ message: received[0], active: false });
    assert.deepEqual(
      presence.map((p) => p[0]),
      ["composing", "paused"],
    );
    fail = true;
    await assert.rejects(
      adapter.send({ message: received[0], text: "reject" }),
      (e) => !JSON.stringify(e.delivery).includes("private-"),
    );
    socket.ev.emit("connection.update", {
      connection: "close",
      lastDisconnect: { error: { output: { statusCode: 401 } } },
    });
    await tick();
    await assert.rejects(
      adapter.send({ message: received[0], text: "offline" }),
    );
    await adapter.login();
    assert.equal(auth.state.creds.registered, false);
    assert.equal(sockets.length, 3);
  } finally {
    await adapter.close();
    await rm(directory, { recursive: true, force: true });
  }
});

test("WhatsApp SQLite credentials, keys, inbox and aliases survive restart and migrate legacy state", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "corki-wa-auth-"));
  const api = {
    BufferJSON: { replacer: (_k, v) => v, reviver: (_k, v) => v },
    initAuthCreds: () => ({ registered: false }),
    proto: { Message: { AppStateSyncKeyData: { fromObject: (v) => v } } },
  };
  let auth;
  try {
    await writeFile(
      path.join(directory, "creds.json"),
      JSON.stringify({ registered: true, me: { id: "11111@s.whatsapp.net" } }),
    );
    await writeFile(
      path.join(directory, "session-22222.0.json"),
      JSON.stringify({ key: "private-key" }),
    );
    await writeFile(
      path.join(directory, "sender-key-22222-1__group.json"),
      JSON.stringify({ key: "escaped-key" }),
    );
    auth = await createAuth(directory, api);
    assert.equal(auth.state.creds.registered, true);
    assert.equal(
      (await auth.state.keys.get("session", ["22222.0"]))["22222.0"].key,
      "private-key",
    );
    assert.equal(
      (await auth.state.keys.get("sender-key", ["22222:1/group"]))[
        "22222:1/group"
      ].key,
      "escaped-key",
    );
    await auth.state.keys.set({ session: { new: { data: "new-key" } } });
    auth.enqueue({
      key: { id: "one", remoteJid: "22222@s.whatsapp.net" },
      message: { conversation: "pending" },
    });
    assert.equal(
      auth.identity(["33333@lid", "22222@s.whatsapp.net"]),
      "22222@s.whatsapp.net",
    );
    await auth.saveCreds();
    auth.close();
    auth = await createAuth(directory, api);
    assert.equal(
      (await auth.state.keys.get("session", ["new"])).new.data,
      "new-key",
    );
    assert.equal(auth.next().item.message.conversation, "pending");
    assert.equal(auth.identity(["33333@lid"]), "22222@s.whatsapp.net");
    const id = auth.next().id;
    auth.acknowledge(id);
    assert.equal(auth.next(), null);
    await auth.state.keys.set({ session: { new: null } });
    assert.equal(
      (await auth.state.keys.get("session", ["new"])).new,
      undefined,
    );
    auth.reset();
    assert.equal(auth.state.creds.registered, false);
    auth.close();
    auth = await createAuth(directory, api);
    assert.equal(auth.state.creds.registered, false);
  } finally {
    auth?.close();
    await rm(directory, { recursive: true, force: true });
  }
});

test("WhatsApp expired QR retries on click; registration restart reconnects without stale callbacks", async () => {
  const auth = await memoryAuth();
  auth.state.creds.registered = false;
  const sockets = [];
  const api = {
    DisconnectReason: {
      loggedOut: 401,
      badSession: 500,
      timedOut: 408,
      restartRequired: 515,
      connectionReplaced: 440,
      forbidden: 403,
      multideviceMismatch: 411,
    },
    default: () => {
      const s = {
        ev: new EventEmitter(),
        end() {},
        user: { id: "11111@s.whatsapp.net" },
        sendMessage: async () => ({ key: { id: "sent" } }),
      };
      sockets.push(s);
      return s;
    },
    normalizeMessageContent: (m) => m,
  };
  const adapter = await whatsapp({
    state_directory: "/unused",
    load: async () => api,
    authFactory: async () => auth,
    require: (name) =>
      name === "pino"
        ? () => ({})
        : { toDataURL: async () => "data:image/png;base64,fixture" },
    incoming: async () => ({ accepted: true }),
  });
  try {
    await adapter.start();
    await adapter.login();
    sockets[0].ev.emit("connection.update", {
      connection: "close",
      lastDisconnect: { error: { output: { statusCode: 408 } } },
    });
    await tick();
    await adapter.login();
    assert.equal(sockets.length, 2);
    sockets[0].ev.emit("connection.update", { connection: "open" });
    await tick();
    await assert.rejects(
      adapter.send({
        message: { conversation: "22222@s.whatsapp.net" },
        text: "not connected",
      }),
    );
    auth.state.creds.registered = true;
    sockets[1].ev.emit("connection.update", {
      connection: "close",
      lastDisconnect: { error: { output: { statusCode: 515 } } },
    });
    for (let i = 0; i < 30 && sockets.length < 3; i++)
      await new Promise((r) => setTimeout(r, 5));
    assert.equal(sockets.length, 3);
    sockets[2].ev.emit("connection.update", { connection: "open" });
    await tick();
    await adapter.send({
      message: { conversation: "22222@s.whatsapp.net" },
      text: "connected",
    });
  } finally {
    await adapter.close();
  }
});

test("WhatsApp admitted inbox survives stop before host acknowledgement and replays once on restart", async () => {
  const auth = await memoryAuth();
  let socket,
    attempts = 0;
  const api = {
    DisconnectReason: { loggedOut: 401 },
    normalizeMessageContent: (m) => m,
    default: () =>
      (socket = {
        ev: new EventEmitter(),
        end() {},
        user: { id: "11111@s.whatsapp.net" },
      }),
  };
  const options = {
    state_directory: "/unused",
    authFactory: async () => auth,
    load: async () => api,
    require: () => () => ({}),
  };
  let adapter = await whatsapp({
    ...options,
    incoming: async () => {
      attempts++;
      throw new Error("host unavailable");
    },
  });
  try {
    await adapter.start();
    socket.ev.emit("connection.update", { connection: "open" });
    await tick();
    socket.ev.emit("messages.upsert", {
      type: "notify",
      messages: [
        {
          key: { id: "one", remoteJid: "22222@s.whatsapp.net" },
          message: { conversation: "preserve me" },
        },
      ],
    });
    await tick();
    assert.equal(attempts, 1);
    assert(auth.next());
    await adapter.close();
    adapter = await whatsapp({
      ...options,
      incoming: async () => {
        attempts++;
        return { accepted: true };
      },
    });
    await adapter.start();
    socket.ev.emit("connection.update", { connection: "open" });
    for (let i = 0; i < 30 && auth.next(); i++) await tick();
    assert.equal(attempts, 2);
    assert.equal(auth.next(), null);
  } finally {
    await adapter.close();
  }
});
