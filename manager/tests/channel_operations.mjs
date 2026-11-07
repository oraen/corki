// Delayed, failing and reordered requests exercise the real channel UI without
// changing the user's accounts or sending external messages.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFile } from "node:fs/promises";
import path from "node:path";
import os from "node:os";
const require = createRequire(
  new URL("../../app/package.json", import.meta.url),
);
const { chromium } = require("playwright");
const browser = await chromium.launch({
  headless: true,
  executablePath:
    process.env.CORKI_TEST_BROWSER ||
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
});
try {
  const page = await browser.newPage({
    viewport: { width: 1280, height: 900 },
  });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("http://corki.test/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith("/channel-icons/"))
      return route.fulfill({
        body: await readFile(path.join("manager/static", url.pathname)),
        contentType: url.pathname.endsWith(".svg")
          ? "image/svg+xml"
          : "image/png",
      });
    return route.fulfill({
      contentType: "text/html",
      body: '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"></head><body><main id="root" style="max-width:1100px;margin:32px auto"></main></body></html>',
    });
  });
  await page.goto("http://corki.test/");
  for (const file of ["style.css", "channels.css", "channel-experience.css"])
    await page.addStyleTag({ path: path.resolve("manager/static", file) });
  for (const file of ["channel-experience.js", "channels.js"])
    await page.addScriptTag({ path: path.resolve("manager/static", file) });
  await page.evaluate(() => {
    const interval = window.setInterval.bind(window);
    window.setInterval = (fn, ms) => interval(fn, Math.min(ms, 80));
    window.operations = [];
    window.toasts = [];
    window.statusCalls = 0;
    window.holdReads = false;
    window.reads = [];
    window.fixture = {
      running: true,
      active: 0,
      interactions: [],
      package_jobs: {},
      installed: { wechat: true, feishu: true, dingtalk: true },
      channels: [
        { id: "wechat", name: "微信", builtin: true, fields: [] },
        { id: "feishu", name: "飞书", builtin: true, fields: [] },
        { id: "dingtalk", name: "钉钉", builtin: true, fields: [] },
      ],
      connections: [
        {
          id: "wx",
          channel: "wechat",
          name: "客服微信",
          enabled: false,
          values: {},
        },
        {
          id: "fs",
          channel: "feishu",
          name: "飞书助手",
          enabled: false,
          values: {},
        },
        {
          id: "dt",
          channel: "dingtalk",
          name: "钉钉助手",
          enabled: true,
          values: {},
        },
      ],
      statuses: { dt: { state: "online" } },
    };
    window.host = {
      providers: [],
      toast: (message) => window.toasts.push(message),
      api: async (endpoint, body) => {
        if (endpoint === "channel-gateway") {
          window.statusCalls++;
          if (window.holdReads)
            return new Promise((resolve) =>
              window.reads.push({
                resolve,
                snapshot: structuredClone(window.fixture),
              }),
            );
          return structuredClone(window.fixture);
        }
        if (body.action === "conversation")
          return {thread:null,sessions:[],messages:[],before:null};
        if (body.action === "applications")
          return { requests: [], before: null };
        if (!["channel", "connection-reconnect"].includes(body.action))
          throw Error("Unexpected mutation " + body.action);
        return new Promise((resolve, reject) =>
          window.operations.push({ body, resolve, reject }),
        );
      },
    };
    window.finish = (index, { fail = false, stale = false } = {}) => {
      const op = window.operations[index];
      if (fail) {
        op.reject(new Error("测试连接失败"));
        return;
      }
      const row = window.fixture.connections.find((c) => c.id === op.body.id);
      if (op.body.action === "channel") row.enabled = op.body.enabled;
      window.fixture.statuses[row.id] = {
        state: row.enabled ? "online" : "stopped",
      };
      const snapshot = structuredClone(window.fixture);
      if (stale) {
        snapshot.connections.find((c) => c.id === "fs").enabled = false;
        snapshot.statuses.fs = { state: "stopped" };
      }
      op.resolve(snapshot);
    };
    window.mount = (id) =>
      window.CorkiChannels.mount(document.querySelector("#root"), {
        ...window.host,
        id,
      });
    window.mount();
  });
  const toggle = (id) => page.locator(`[data-toggle="${id}"]`);
  const row = (id) => toggle(id).locator("xpath=ancestor::tr");
  await toggle("wx").waitFor();
  await toggle("wx").click();
  assert(await toggle("wx").isDisabled());
  assert.match(await row("wx").textContent(), /开启中/);
  assert(await toggle("fs").isEnabled());
  assert(await toggle("dt").isEnabled());
  assert(
    await page.locator('[data-config="wx"]').isEnabled(),
    "management remains accessible",
  );
  assert(await page.locator('[data-act="add-connection"]').isEnabled());
  assert.equal(
    await page.locator(".channel-progress").count(),
    0,
    "no page-wide progress lock",
  );
  await page.evaluate(() =>
    document.querySelector('[data-toggle="wx"]').click(),
  );
  assert.equal(
    await page.evaluate(() => window.operations.length),
    1,
    "duplicate click is ignored",
  );
  const polls = await page.evaluate(() => window.statusCalls);
  await page.waitForFunction((n) => window.statusCalls > n, polls);
  await page.evaluate(() => (window.holdReads = true));
  await toggle("fs").click();
  assert.match(await row("fs").textContent(), /开启中/);
  await page.screenshot({
    path: path.join(os.tmpdir(), "corki-channel-operations.png"),
  });
  await page.evaluate(() => window.finish(1));
  await page.waitForFunction(
    () =>
      document
        .querySelector('[data-toggle="fs"]')
        .getAttribute("aria-checked") === "true",
  );
  await page.evaluate(() => window.finish(0, { stale: true }));
  await page.waitForFunction(
    () => !document.querySelector('[data-toggle="wx"]').disabled,
  );
  assert.equal(
    await toggle("fs").getAttribute("aria-checked"),
    "true",
    "late A snapshot cannot revert B",
  );
  // A status read captured before the mutations completed must also be discarded.
  await page.evaluate(() => {
    window.holdReads = false;
    window.reads.splice(0).forEach((r) => r.resolve(r.snapshot));
  });
  await page.waitForFunction(
    () =>
      window.reads.length === 0 &&
      document
        .querySelector('[data-toggle="wx"]')
        .getAttribute("aria-checked") === "true",
  );
  assert.equal(await toggle("fs").getAttribute("aria-checked"), "true");

  // Navigate away and back while a close request is outstanding.
  await toggle("wx").click();
  assert.match(await row("wx").textContent(), /关闭中/);
  await page.evaluate(() => window.mount("wx"));
  assert.equal(await page.locator('[data-connection-overview]').count(),0);
  await page.locator('[data-channel-tab="settings"]').click();
  await page.locator("#channel-pane-settings").waitFor({ state: "visible" });
  await page.evaluate(() => window.mount());
  await toggle("wx").waitFor();
  assert(await toggle("wx").isDisabled());
  assert(await toggle("fs").isEnabled());
  assert.equal(await page.evaluate(() => window.operations.length), 3);
  await page.evaluate(() => window.finish(2, { fail: true }));
  await page.waitForFunction(
    () => !document.querySelector('[data-toggle="wx"]').disabled,
  );
  assert.equal(
    await toggle("wx").getAttribute("aria-checked"),
    "true",
    "failed close restores current enabled state",
  );
  assert.match(
    await page.evaluate(() => window.toasts.at(-1)),
    /客服微信.*测试连接失败/,
  );

  // Settings and tabs remain usable while disconnecting from the list switch.
  await toggle("wx").click();
  await page.evaluate(() => window.mount("wx"));
  assert.equal(await page.locator("dialog").count(), 0);
  assert(await page.locator('[data-channel-tab="settings"]').isEnabled());
  await page.locator('[data-channel-tab="history"]').click();
  await page
    .locator("#channel-pane-history")
    .waitFor({ state: "visible" });
  assert(
    await page.locator("[data-reset-conversation]").isDisabled(),
    "conflicting reset waits for this account",
  );
  await page.evaluate(() => {
    window.CorkiChannels.unmount();
    window.finish(3);
  });
  await page.waitForFunction(() =>
    window.toasts.some((t) => t === "客服微信：已关闭"),
  );
  await page.evaluate(() => window.mount());
  await toggle("wx").waitFor();
  assert(await toggle("wx").isEnabled());
  assert.equal(await toggle("wx").getAttribute("aria-checked"), "false");
  assert.deepEqual(errors, []);
  console.log(
    "Per-account pending, parallel toggles, reordered responses, polling, navigation, failure recovery and nonblocking confirmation passed.",
  );
} finally {
  await browser.close();
}
