// Isolated browser fixtures; no account login or external messages.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { existsSync } from "node:fs";
import path from "node:path";
const require = createRequire(new URL("../../app/package.json", import.meta.url));
const { chromium } = require("playwright");
const executablePath = process.env.CORKI_TEST_BROWSER || [
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
].find(existsSync);
const browser = await chromium.launch({ headless: true, ...(executablePath ? { executablePath } : {}) });
try {
  const page = await browser.newPage();
  await page.route("http://corki.test/**", route => route.fulfill({
    contentType: "text/html", body: '<html><body><div id="root"></div></body></html>',
  }));
  await page.goto("http://corki.test/");
  await page.evaluate(() => {
    const interval = window.setInterval.bind(window);
    window.setInterval = (fn, ms) => interval(fn, Math.min(ms, 100));
    window.qrCalls = 0;
    window.fixture = {
      running: true, active: 0, interactions: [], receipts: [], conversations: [], package_jobs: {},
      channels: [
        { id: "wechat", name: "微信", builtin: true, fields: [] },
        { id: "dingtalk", name: "钉钉", builtin: true, fields: [] },
        { id: "whatsapp", name: "WhatsApp", builtin: true, fields: [] },
      ],
      connections: [
        { id: "wx", channel: "wechat", name: "微信测试", enabled: true, values: {} },
        { id: "dt", channel: "dingtalk", name: "钉钉测试", enabled: true, values: {} },
        { id: "wa", channel: "whatsapp", name: "WhatsApp测试", enabled: true, values: {} },
      ],
      installed: { wechat: true, dingtalk: true, whatsapp: true },
      statuses: {
        wx: { state: "login" }, dt: { state: "online" },
        wa: { state: "qr", qr: "data:image/png;base64,fixture" },
      },
    };
    window.testHost = {
      toast: () => {}, providers: [],
      api: async (endpoint, body) => {
        if (endpoint === "mutate") {
          if (body.action !== "wechat-login" || body.id !== "wx") throw Error("unexpected mutation");
          window.qrCalls++;
          window.fixture.statuses.wx = { state: "qr", qr: "data:image/png;base64,fixture-" + window.qrCalls };
        }
        return structuredClone(window.fixture);
      },
    };
  });
  await page.addScriptTag({ path: path.resolve("manager/static/channel-experience.js") });
  await page.addScriptTag({ path: path.resolve("manager/static/channels.js") });
  await page.evaluate(() => window.CorkiChannels.mount(document.querySelector("#root"), window.testHost));
  await page.locator('[data-login="wx"]').waitFor();
  assert.equal(await page.evaluate(() => window.qrCalls), 0);
  assert.equal(await page.locator('[data-login="wx"]').textContent(), "扫码登录");
  assert.equal(await page.locator('[data-login="dt"]').count(), 0);
  await page.locator('[data-login="wx"]').click();
  await page.locator('.channel-login .channel-qr-frame img').waitFor();
  assert.equal(await page.evaluate(() => window.qrCalls), 1);
  await page.evaluate(() => {
    window.fixture.statuses.wx = { state: "login", message: "二维码已过期，请点击重新获取" };
  });
  await page.getByRole("button", { name: "重新获取二维码" }).waitFor();
  assert.equal(await page.locator('.channel-login .channel-qr-frame img').count(), 0);
  assert.equal(await page.locator('[data-connection-error="wx"]').count(), 0);
  await page.getByRole("button", { name: "重新获取二维码" }).click();
  await page.locator('.channel-login .channel-qr-frame img').waitFor();
  assert.equal(await page.evaluate(() => window.qrCalls), 2);
  await page.locator('.channel-dialog [aria-label="关闭"]').click();
  await page.locator('[data-login="wa"]').click();
  await page.locator('.channel-login img').waitFor();
  assert.equal(await page.evaluate(() => window.qrCalls), 2, "other channels must not request WeChat login");
  await page.evaluate(() => window.CorkiChannels.unmount());
  console.log("WeChat browser login/retry passed; DingTalk and WhatsApp behavior unchanged.");
} finally {
  await browser.close();
}
