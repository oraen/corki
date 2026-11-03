// Isolated product flow and visual checks: no real credentials or messages.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFile, mkdir } from "node:fs/promises";
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
const artifacts =
  process.env.CORKI_TEST_ARTIFACT_DIR ||
  path.join(os.tmpdir(), "corki-channel-experience");
await mkdir(artifacts, { recursive: true });
try {
  const page = await browser.newPage({
    viewport: { width: 1280, height: 1000 },
  });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route("http://corki.test/**", async (route) => {
    const url = new URL(route.request().url());
    if (
      url.pathname.startsWith("/channel-icons/") ||
      url.pathname === "/corki-icon-ears.png"
    ) {
      return route.fulfill({
        body: await readFile(path.join("manager/static", url.pathname)),
        contentType: url.pathname.endsWith(".svg")
          ? "image/svg+xml"
          : "image/png",
      });
    }
    return route.fulfill({
      contentType: "text/html",
      body: '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"></head><body><main id="root" style="max-width:1100px;margin:40px auto;padding:16px"></main></body></html>',
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
    window.mutations = [];
    window.holdQR = false;
    window.fixture = {
      running: true,
      active: 0,
      interactions: [],
      installed: { wechat: true, feishu: true, dingtalk: true },
      package_jobs: {},
      channels: [
        { id: "wechat", name: "微信", builtin: true, fields: [] },
        {
          id: "feishu",
          name: "飞书",
          builtin: true,
          fields: [
            { key: "app_id", label: "App ID", required: true },
            {
              key: "app_secret",
              label: "App Secret",
              required: true,
              secret: true,
            },
            { key: "region", label: "平台", options: ["飞书", "Lark"] },
          ],
        },
        {
          id: "dingtalk",
          name: "钉钉",
          builtin: true,
          fields: [
            { key: "client_id", label: "Client ID（AppKey）", required: true },
            {
              key: "client_secret",
              label: "Client Secret（AppSecret）",
              secret: true,
              required: true,
            },
          ],
        },
      ],
      connections: [
        {
          id: "wx",
          name: "客户服务微信",
          channel: "wechat",
          enabled: true,
          values: { session_validation: "true" },
        },
        {
          id: "fs",
          name: "团队助手",
          channel: "feishu",
          enabled: false,
          values: {
            app_id: "cli_0123456789abcdef",
            session_validation: "true",
          },
        },
        {
          id: "dt",
          name: "钉钉",
          channel: "dingtalk",
          enabled: true,
          values: {},
        },
      ],
      statuses: { wx: { state: "login" }, dt: { state: "online" } },
    };
    window.host = {
      providers: [],
      toast: () => {},
      api: async (endpoint, body) => {
        if (endpoint === "mutate") {
          if (body.action === "applications")
            return { requests: [], before: null };
          window.mutations.push({
            ...body,
            code: body.code ? "[redacted]" : undefined,
          });
          if (body.action === "wechat-login") {
            if (window.holdQR)
              await new Promise((resolve) => (window.releaseQR = resolve));
            window.fixture.statuses.wx = {
              state: "qr",
              qr:
                "data:image/svg+xml," +
                encodeURIComponent(
                  '<svg xmlns="http://www.w3.org/2000/svg" width="232" height="232"><rect width="232" height="232" fill="white"/><path d="M10 10h60v60H10zM160 10h60v60h-60zM10 160h60v60H10zM100 100h30v30h-30zM160 160h60v60h-60z" fill="#31342d"/></svg>',
                ),
            };
          }
          if (body.action === "channel")
            window.fixture.connections.find((c) => c.id === body.id).enabled =
              body.enabled;
          if (body.action === "connection-reconnect")
            window.fixture.statuses[body.id] = { state: "connecting" };
        }
        return structuredClone(window.fixture);
      },
    };
    window.mount = (id) =>
      window.CorkiChannels.mount(document.querySelector("#root"), {
        ...window.host,
        id,
      });
    window.mount("wx");
  });
  await page.locator('[data-connection-primary="login"]').waitFor();
  assert.equal(await page.locator(".channel-message-composer").count(), 0);
  await page.evaluate(() => (window.holdQR = true));
  await page.locator('[data-connection-primary="login"]').click();
  await page.locator(".channel-qr-loading").waitFor();
  await page.screenshot({ path: path.join(artifacts, "wechat-loading.png") });
  await page.evaluate(() => {
    window.holdQR = false;
    window.releaseQR();
  });
  await page.locator(".channel-qr-frame>img").waitFor();
  await page.screenshot({ path: path.join(artifacts, "wechat-qr.png") });
  const scanLayout = () =>
    page.locator(".channel-wechat-login").evaluate((dialog) => {
      const rect = (node) => {
        const r = node.getBoundingClientRect();
        return { x: r.x, y: r.y, width: r.width, height: r.height };
      };
      const icon = dialog
        .querySelector("[data-wechat-refresh] svg")
        .getBoundingClientRect();
      return {
        dialog: rect(dialog),
        title: rect(dialog.querySelector("h2")),
        frame: rect(dialog.querySelector(".channel-qr-frame")),
        button: rect(dialog.querySelector("[data-wechat-refresh]")),
        text: rect(dialog.querySelector("[data-wechat-refresh] span")),
        icon: { x: icon.x + icon.width / 2, y: icon.y + icon.height / 2 },
      };
    });
  const sameLayout = (before, after) => {
    for (const part of Object.keys(before))
      for (const key of Object.keys(before[part]))
        assert(
          Math.abs(before[part][key] - after[part][key]) < 0.75,
          `${part}.${key} moved during QR refresh: ${JSON.stringify({ before, after })}`,
        );
  };
  const initialLayout = await scanLayout();
  await page.evaluate(() => (window.holdQR = true));
  await page.locator("[data-wechat-refresh]").click();
  await page.locator(".channel-qr-loading").waitFor();
  assert.equal(
    await page.locator("[data-wechat-refresh]").textContent(),
    "刷新二维码",
  );
  sameLayout(initialLayout, await scanLayout());
  await page.screenshot({
    path: path.join(artifacts, "wechat-refreshing.png"),
  });
  await page.evaluate(() => {
    window.holdQR = false;
    window.releaseQR();
  });
  await page.locator(".channel-qr-frame>img").waitFor();
  sameLayout(initialLayout, await scanLayout());
  assert.equal(
    await page.evaluate(
      () =>
        window.mutations.filter((m) => m.action === "wechat-login").at(-1)
          .refresh,
    ),
    true,
  );
  await page.evaluate(
    () =>
      (window.fixture.statuses.wx = {
        ...window.fixture.statuses.wx,
        state: "scanned",
      }),
  );
  await page.getByRole("heading", { name: "已扫码，请在手机上确认" }).waitFor();
  await page.evaluate(
    () => (window.fixture.statuses.wx = { state: "verification" }),
  );
  const input = page.locator("#wechat-verification-code");
  await input.waitFor();
  await page.locator("[data-wechat-verify]").click();
  assert.match(
    await page.locator("#wechat-verification-error").textContent(),
    /数字验证码/,
  );
  await input.fill("123456");
  await page.waitForTimeout(180);
  assert.equal(
    await input.inputValue(),
    "123456",
    "polling must preserve the entered verification code",
  );
  await input.press("Enter");
  assert.equal(
    await page.evaluate(
      () => window.mutations.filter((m) => m.action === "verification").length,
    ),
    1,
  );
  await page.evaluate(
    () =>
      (window.fixture.statuses.wx = {
        state: "login",
        message: "二维码已过期，请点击重新获取",
      }),
  );
  await page.getByRole("heading", { name: "二维码已过期" }).waitFor();
  await page.screenshot({ path: path.join(artifacts, "wechat-expired.png") });
  await page.evaluate(() => (window.fixture.statuses.wx = { state: "online" }));
  await page.locator("[data-wechat-history]").waitFor();
  assert.equal(await page.locator(".channel-login p").count(), 0);
  assert.equal(
    await page
      .locator(".channel-wechat-login .channel-dialog-foot")
      .isVisible(),
    false,
  );
  await page.screenshot({ path: path.join(artifacts, "wechat-success.png") });
  await page.locator("[data-wechat-history]").click();
  await page.locator(".channel-applications").waitFor({ state: "visible" });
  assert.match(
    await page.locator(".channel-applications-content").textContent(),
    /暂无会话/,
  );
  await page.locator('[data-connection-primary="reconnect"]').click();
  assert.match(
    await page.locator(".channel-dialog").textContent(),
    /会话历史会保留/,
  );
  await page.locator('.channel-dialog button[type="submit"]').click();
  await page.locator('[data-connection-primary="waiting"]').waitFor();
  await page.evaluate(() => window.mount("fs"));
  await page.locator('[data-connection-primary="enable"]').waitFor();
  await page.locator("[data-connection-settings]").click();
  assert.equal(await page.locator(".channel-setup-guide").count(), 0);
  assert.equal(await page.locator("[data-wechat-refresh]").count(), 0);
  await page.screenshot({
    path: path.join(artifacts, "feishu-settings.png"),
    fullPage: true,
  });
  await page.locator('[data-connection-primary="enable"]').click();
  assert.equal(
    await page.evaluate(() => window.mutations.at(-1).action),
    "channel",
  );
  await page.evaluate(() => window.mount("dt"));
  await page.locator(".channel-management").waitFor();
  assert.equal(await page.locator("[data-connection-overview]").count(), 0);
  assert.equal(await page.locator(".channel-message-composer").count(), 1);
  await page.evaluate(() => window.mount("wx"));
  await page.locator(".channel-management").waitFor();
  await page.evaluate(() => (window.fixture.statuses.wx = { state: "login" }));
  await page.locator('[data-connection-primary="login"]').waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('[data-connection-primary="login"]').click();
  await page.locator(".channel-qr-frame>img").waitFor();
  await page.screenshot({ path: path.join(artifacts, "wechat-mobile.png") });
  assert(
    await page
      .locator(".channel-dialog")
      .evaluate((el) => el.getBoundingClientRect().width <= innerWidth),
  );
  const mobileLayout = await scanLayout();
  await page.evaluate(() => (window.holdQR = true));
  await page.locator("[data-wechat-refresh]").click();
  await page.locator(".channel-qr-loading").waitFor();
  sameLayout(mobileLayout, await scanLayout());
  await page.evaluate(() => {
    window.holdQR = false;
    window.releaseQR();
  });
  await page.locator(".channel-qr-frame>img").waitFor();
  sameLayout(mobileLayout, await scanLayout());
  await page.evaluate(() => (window.fixture.statuses.wx = { state: "online" }));
  await page.locator("[data-wechat-history]").waitFor();
  await page.screenshot({
    path: path.join(artifacts, "wechat-success-mobile.png"),
  });
  await page.locator('.channel-dialog [aria-label="关闭"]').click();
  await page.setViewportSize({ width: 1280, height: 1000 });
  await page.evaluate(() => window.mount());
  await page.locator('[data-act="add-connection"]').waitFor();
  for (const type of ["wechat", "feishu", "dingtalk"]) {
    await page.locator('[data-act="add-connection"]').click();
    await page.locator(".channel-dialog .channel-select summary").click();
    await page.locator(`.channel-dialog [data-option="${type}"]`).click();
    await page.getByRole("button", { name: "下一步", exact: true }).click();
    await page.locator(".channel-connect-dialog").waitFor();
    await page.screenshot({ path: path.join(artifacts, `${type}-create.png`) });
    if (["wechat", "feishu"].includes(type))
      assert.equal(
        await page
          .locator(
            ".channel-dialog .channel-setup-guide,.channel-dialog .channel-help",
          )
          .count(),
        0,
      );
    await page.locator('.channel-dialog [aria-label="关闭"]').click();
  }
  assert.deepEqual(errors, []);
  console.log(
    "WeChat states, stable QR refresh layout, verification, reconnect, Feishu setup, mobile layout and DingTalk isolation passed. Screenshots: " +
      artifacts,
  );
} finally {
  await browser.close();
}
