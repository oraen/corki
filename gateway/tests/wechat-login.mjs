import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, writeFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { create } from "../connectors/wechat.mjs";

const reply = value => ({ ok: true, json: async () => value });
async function until(predicate) {
  for (let i = 0; i < 400; i++) {
    if (predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 5));
  }
  assert.fail("WeChat login did not reach the expected state");
}
async function fixture(run, { cached, qrStatus, failQR = false, expiredToken = false } = {}) {
  const directory = await mkdtemp(path.join(os.tmpdir(), "corki-wechat-login-"));
  const fetch = globalThis.fetch, write = process.stdout.write;
  const states = [], calls = { qr: 0, poll: 0, updates: 0 };
  let adapter, started;
  process.stdout.write = function (chunk, ...args) {
    try {
      const value = JSON.parse(String(chunk));
      if (value.method === "status") states.push(value.params);
    } catch {}
    return write.call(this, chunk, ...args);
  };
  try {
    if (cached) await writeFile(path.join(directory, "login.json"), JSON.stringify({
      id: "saved-bot", token: "saved-token", base: "https://ilinkai.weixin.qq.com",
    }));
    globalThis.fetch = async (url, request) => {
      if (url.includes("get_bot_qrcode")) {
        calls.qr++;
        if (failQR && calls.qr === 1) throw new Error("fixture network failure");
        return reply({ qrcode: "fixture-" + calls.qr, qrcode_img_content: "fixture-image" });
      }
      if (url.includes("get_qrcode_status")) {
        calls.poll++;
        return reply(await qrStatus?.(calls, request) || {
          status: "confirmed", bot_token: "new-token", ilink_bot_id: "new-bot",
          baseurl: "https://ilinkai.weixin.qq.com",
        });
      }
      if (url.includes("getupdates")) {
        calls.updates++;
        if (expiredToken && calls.updates === 1) return reply({ errcode: -14 });
        return new Promise((_, reject) => {
          request.signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
        });
      }
      throw new Error("Unexpected WeChat request");
    };
    adapter = await create({
      state_directory: directory, cursor: "", incoming: async () => ({ accepted: true }),
      require: () => ({ toDataURL: async () => "data:image/png;base64,fixture" }),
    });
    started = adapter.start();
    await run({ adapter, started, states, calls, directory });
  } finally {
    await adapter?.close();
    await started;
    globalThis.fetch = fetch;
    process.stdout.write = write;
    await rm(directory, { recursive: true, force: true });
  }
}

test("refresh cancels stale QR confirmation and persists only the new login", () => fixture(
  async ({ adapter, calls, states, directory }) => {
    await adapter.login();
    await until(() => calls.poll === 1);
    await adapter.login({ refresh: true });
    await until(() => states.some(s => s.state === "online"));
    assert.equal(calls.qr, 2);
    assert.equal(JSON.parse(await readFile(path.join(directory, "login.json"), "utf8")).id, "new-bot");
    assert(!states.some(s => s.state === "error"), "intentional cancellation is not a connection error");
  },
  { qrStatus: async calls => {
    if(calls.qr !== 1) return undefined;
    await new Promise(resolve=>setTimeout(resolve,50));
    return {status:"confirmed",bot_token:"stale-token",ilink_bot_id:"stale-bot"};
  } },
));

test("WeChat enabling waits without requesting or expiring a QR code", () => fixture(async ({
  adapter, calls, states,
}) => {
  await until(() => states.some(s => s.state === "login"));
  assert.deepEqual(calls, { qr: 0, poll: 0, updates: 0 });
  await adapter.close();
  assert(!states.some(s => s.state === "error" || s.state === "qr"));
}));

test("WeChat click requests one QR; expiry waits for another click instead of failing", () => fixture(
  async ({ adapter, calls, states, directory }) => {
    await Promise.all([adapter.login(), adapter.login()]);
    await until(() => states.some(s => s.state === "login" && s.code === "expired"));
    assert.equal(calls.qr, 1);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(calls.qr, 1, "expiry must not silently request another QR");
    assert(!states.some(s => s.state === "error"));
    await adapter.login();
    await until(() => states.some(s => s.state === "online"));
    assert.equal(calls.qr, 2);
    assert.equal(JSON.parse(await readFile(path.join(directory, "login.json"), "utf8")).id, "new-bot");
    await adapter.login();
    assert.equal(calls.qr, 2, "a logged-in account must not start a new login");
  },
  { qrStatus: calls => calls.qr === 1 ? { status: "expired" } : undefined },
));

test("WeChat QR request failure can be retried without toggling the connection", () => fixture(
  async ({ adapter, calls, states }) => {
    await adapter.login();
    await until(() => states.some(s => s.code === "login_failed"));
    assert.equal(calls.qr, 1);
    await adapter.login();
    await until(() => states.some(s => s.state === "online"));
    assert.equal(calls.qr, 2);
  }, { failQR: true },
));

test("WeChat saved credentials reconnect without generating a QR", () => fixture(
  async ({ adapter, calls, states }) => {
    await until(() => calls.updates === 1);
    assert(states.some(s => s.state === "online"));
    assert.equal(calls.qr, 0);
    await adapter.login();
    assert.equal(calls.qr, 0);
  }, { cached: true },
));

test("WeChat expired saved credentials wait for an explicit new scan", () => fixture(
  async ({ adapter, calls, states }) => {
    await until(() => states.some(s => s.state === "login" && s.code === "credentials"));
    assert.equal(calls.qr, 0);
    await adapter.login();
    await until(() => calls.updates === 2);
    assert.equal(calls.qr, 1);
    assert.equal(states.at(-1).state, "online");
  }, { cached: true, expiredToken: true },
));
