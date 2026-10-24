import { poll } from './wait.mjs';
import { python, systemPath, childProcesses } from './platform.mjs';
import { _electron as electron } from "playwright";
import http from "node:http";
import { mkdtemp, writeFile, readFile, rm, mkdir } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import assert from "node:assert/strict";
const home = await mkdtemp(path.join(os.tmpdir(), "corki-electron-e2e-"));
const root = path.resolve("..");
let requestCount = 0;
const server = http.createServer(async (req, res) => {
  requestCount++;
  let raw = "";
  for await (const chunk of req) raw += chunk;
  const body = JSON.parse(raw);
  assert.equal(body.model, "fixture-model");
  await new Promise(resolve => setTimeout(resolve, 1500));
  res.writeHead(200, { "Content-Type": "text/event-stream" });
  const slow = String(body.messages.findLast(m => m.role === "user")?.content).includes("slow task");
  const text = slow
    ? "流式输出用于验证停止和会话切换。".repeat(
        process.env.CORKI_TEST_RECOVERY ? 150 : 25,
      )
    : '# 测试完成\n\n**真实 Agent** 已连接。\n\n```python\nprint("hello")\n```';
  for (const piece of text.match(/.{1,4}|\n/g) || []) {
    if (res.destroyed) return;
    res.write(
      `data: ${JSON.stringify({ choices: [{ index: 0, delta: { content: piece }, finish_reason: null }] })}\n\n`,
    );
    await new Promise((r) => setTimeout(r, slow ? 80 : 5));
  }
  res.end(
    'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n',
  );
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
await writeFile(
  path.join(home, "settings.json"),
  JSON.stringify({
    default: { provider: "fixture", model: "fixture-model" },
    providers: {
      fixture: {
        base_url: `http://127.0.0.1:${server.address().port}/v1`,
        api_mode: "chat_completions",
        api_key: "fixture-not-a-credential",
        models: { "fixture-model": { context_window: 262144 } },
      },
    },
    features: { goals: false },
    skills: { enabled: false },
  }),
);
let app;
try {
  const env = {
    ...process.env,
    CORKI_HOME: home,
    CORKI_PYTHON:
      python,
  };
  delete env.ELECTRON_RUN_AS_NODE;
  const packaged = process.env.CORKI_TEST_PACKAGED;
  if (packaged) {
    await mkdir(path.join(home, "app"), { recursive: true });
    await writeFile(
      path.join(home, "app", "window.json"),
      JSON.stringify({ python: env.CORKI_PYTHON }),
    );
    delete env.CORKI_PYTHON;
    delete env.PYTHONPATH;
    delete env.VIRTUAL_ENV;
    env.PATH = systemPath;
  }
  const launchDirectory = path.join(home, "快速启动 中文目录");
  await mkdir(launchDirectory, {recursive:true});
  const launchArgs = ["--corki-directory", launchDirectory];
  app = await electron.launch({
    ...(packaged
      ? { executablePath: path.resolve(packaged), args: launchArgs, cwd: home }
      : { args: [path.resolve("."), ...launchArgs] }),
    env,
    timeout: 30000,
  });
  assert.equal(
    await app.evaluate(({ app }) => app.isPackaged),
    Boolean(packaged),
  );
  await app.evaluate(({ dialog }) => {
    dialog.showMessageBox = async () => ({
      response: 1,
      checkboxChecked: false,
    });
  });
  let page = await app.firstWindow();
  const errors = [];
  page.on("pageerror", (e) => {
    errors.push(e.message);
    console.error("Page error:", e.message);
  });
  page.on("console", (m) => {
    if (m.type() === "error") console.error("Console:", m.text());
  });
  await page
    .getByRole("heading", { name: "Learning never ends" })
    // A cold Windows Python host can take longer under software virtualization.
    // Match the bridge's bounded startup deadline; ordinary UI waits stay short.
    .waitFor({ timeout: process.platform === "win32" ? 120000 : 30000 });
  await page.getByRole("button", { name: "选择模型", exact: true }).waitFor();
  await page.waitForFunction(directory => document.querySelector('.composer-area')?.textContent.includes(directory), launchDirectory);
  assert.equal(await page.evaluate(()=>window.corki.takeLaunchDirectory()), null);
  const secondDirectory = path.join(home, '第二个 工作目录');
  await mkdir(secondDirectory);
  await app.evaluate(({app}, directory)=>app.emit('second-instance', {}, ['Corki','--corki-directory',directory]), secondDirectory);
  await page.waitForFunction(directory => document.querySelector('.composer-area')?.textContent.includes(directory), secondDirectory);

  await page.evaluate(() => {
    window.__events = [];
    window.corki.subscribe((e) =>
      window.__events.push({
        type: e.type,
        id: e.data.item_id,
        turn: e.data.turn_id,
      }),
    );
  });
  const composer = page.getByRole("textbox", { name: "消息", exact: true });
  await composer.fill("草稿恢复");
  await page.waitForTimeout(300);
  await page.reload();
  await page.getByRole("button", { name: "选择模型", exact: true }).waitFor();
  await page.waitForFunction(
    () =>
      document.querySelector('textarea[aria-label="消息"]')?.value ===
      "草稿恢复",
  );
  assert.equal(await composer.inputValue(), "草稿恢复");
  await composer.dispatchEvent("compositionstart");
  await composer.dispatchEvent("keydown", {
    key: "Enter",
    code: "Enter",
    keyCode: 229,
    isComposing: true,
    bubbles: true,
  });
  await composer.dispatchEvent("compositionend");
  assert.equal(await page.locator(".message.user").count(), 0);
  assert.equal(await composer.inputValue(), "草稿恢复");
  await composer.fill("hello desktop");
  await page.getByRole("button", { name: "发送消息" }).click();
  await page.locator(".message.user").filter({hasText:"hello desktop"}).waitFor({timeout:1000});
  assert.equal(await page.locator(".message.assistant").count(), 0);
  await page.getByRole("status", {name:"思考中",exact:true}).waitFor();
  await page.screenshot({path:path.join(home,"thinking.png")});
  await page
    .locator(".markdown strong")
    .filter({ hasText: "真实 Agent" })
    .waitFor({ timeout: 30000 });
  await page.getByRole("button", { name: "复制回答原文" }).waitFor();

  assert.equal(await page.locator(".message.assistant").count(), 1);
  assert.equal(await page.locator(".thinking-indicator").count(), 0);
  assert.equal(await page.locator(".message.user").count(), 1);
  await page.screenshot({ path: path.join(home, "native-chat.png") });
  await page.locator(".new-chat").click();
  assert.equal(await page.locator(".message.user").count(), 0);
  await page
    .getByRole("textbox", { name: "消息", exact: true })
    .fill("slow task");
  await page.getByRole("button", { name: "发送消息" }).click();
  await page
    .getByRole("button", { name: "停止执行" })
    .waitFor({ timeout: 30000 });
  await page.locator(".markdown").filter({ hasText: "流式输出" }).waitFor();
  await page.locator(".new-chat").click();
  await page.getByRole("button", { name: "slow task", exact: true }).click();
  // Closing a macOS window must leave the native host and active Agent alive.
  if (process.platform === "darwin") {
    const closed = page.waitForEvent("close");
    await app.evaluate(({ BrowserWindow }) =>
      BrowserWindow.getAllWindows()[0].close(),
    );
    await closed;
    assert.equal(
      await app.evaluate(
        ({ BrowserWindow }) => BrowserWindow.getAllWindows().length,
      ),
      0,
    );
    const reopened = app.waitForEvent("window");
    await app.evaluate(({ app }) => app.emit("activate"));
    page = await reopened;
    await page
      .getByRole("button", { name: "停止执行" })
      .waitFor({ timeout: 30000 });
    await page.locator(".markdown").filter({ hasText: "流式输出" }).waitFor();
  }
  const partialBeforeStop = await page.locator(".message.assistant .markdown").last().innerText();
  await page.getByRole("button", { name: "停止执行" }).click();
  await page
    .getByRole("button", { name: "发送消息" })
    .waitFor({ timeout: 15000 });
  assert(
    !(await page.locator(".inline-error").allTextContents()).some((t) =>
      t.includes("CancelledError"),
    ),
  );
  await page.locator(".message-stopped").filter({hasText:"回答已停止"}).waitFor();
  const partialAfterStop = await page.locator(".message.assistant .markdown").last().innerText();
  assert(partialAfterStop.startsWith(partialBeforeStop));
  assert.equal(await page.locator('.message.assistant img[alt="Corki"]').count(), 1);
  await page.reload();
  await page
    .getByRole("button", { name: "选择模型", exact: true })
    .waitFor({ timeout: 30000 });
  assert.equal(await page.locator(".thread-select").count(), 2);
  await page.locator(".message-stopped").filter({hasText:"回答已停止"}).waitFor();
  assert.equal(await page.locator(".message.assistant .markdown").last().innerText(), partialAfterStop);
  assert.equal(await page.locator('.message.assistant img[alt="Corki"]').count(), 1);
  await page.getByRole("textbox",{name:"消息",exact:true}).fill("Continue after stopping");
  await page.getByRole("button",{name:"发送消息"}).click();
  await page.locator(".markdown strong").filter({hasText:"真实 Agent"}).waitFor();
  assert.equal(await page.locator(".message-stopped").count(),1);
  await page.getByRole("button",{name:"发送消息"}).waitFor();
  assert.deepEqual(errors, []);
  if (process.env.CORKI_TEST_RECOVERY) {
    await page.locator(".new-chat").click();
    await page
      .getByRole("textbox", { name: "消息", exact: true })
      .fill("slow task recovery");
    await page.getByRole("button", { name: "发送消息" }).click();
    await page.locator(".markdown").filter({ hasText: "流式输出" }).waitFor();
    const requestsBefore = requestCount;
    // A renderer crash is a view failure, not authority to interrupt/replay Agent work.
    await app.evaluate(
      ({ BrowserWindow }) =>
        new Promise((resolve) => {
          const contents = BrowserWindow.getAllWindows()[0].webContents;
          contents.once("did-finish-load", () => resolve(true));
          contents.forcefullyCrashRenderer();
        }),
    );
    const recovered = await app.evaluate(async ({ BrowserWindow }) => {
      const contents = BrowserWindow.getAllWindows()[0].webContents;
      if (contents.isCrashed()) return false;
      return contents.executeJavaScript(`new Promise(resolve => {
        const check = () => !!document.querySelector('button[aria-label="停止执行"]');
        if(check()) return resolve(true);
        const observer = new MutationObserver(() => { if(check()){clearTimeout(timer);observer.disconnect();resolve(true)} });
        const timer = setTimeout(() => {observer.disconnect();resolve(false)},10000);
        observer.observe(document.body,{childList:true,subtree:true,attributes:true});
      })`);
    });
    assert.equal(
      recovered,
      true,
      "The recovered native renderer must show the still-running task",
    );
    assert.equal(requestCount, requestsBefore);
    // Playwright keeps its old Page marked as crashed. Verify the recovered
    // renderer through the native host instead of closing the last Windows
    // window (which correctly quits the application on that platform).
    const nativeDOM = source => app.evaluate(({BrowserWindow}, source) =>
      BrowserWindow.getAllWindows()[0].webContents.executeJavaScript(source), source);
    const parent = await app.evaluate(() => process.pid);
    const normalize = value => value.replaceAll('\\', '/').toLowerCase();
    const owned = childProcesses(parent).filter(row =>
      normalize(row.command).includes('bridge/main.py') &&
      normalize(row.command).includes(normalize(home)));
    assert.equal(
      owned.length,
      1,
      "Only the disposable test's Python host may be terminated",
    );
    process.kill(owned[0].pid, "SIGKILL");
    await poll(() => nativeDOM(`!!document.querySelector('button[aria-label="重新连接"]') || [...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='重新连接')`));
    await nativeDOM(`([...document.querySelectorAll('button')].find(b=>b.getAttribute('aria-label')==='重新连接' || b.textContent.trim()==='重新连接')).click()`);
    await poll(() => nativeDOM(`[...document.querySelectorAll('.message-error')].some(e=>e.textContent.includes('上次执行意外断开'))`), {timeout:30000});
    assert.equal(
      requestCount,
      requestsBefore,
      "A lost Agent process must not replay its model/tool requests",
    );
    assert.equal(
      await nativeDOM(`document.querySelectorAll('button[aria-label="停止执行"]').length`),
      0,
    );
    assert(
      (await nativeDOM(`[...document.querySelectorAll('.markdown')].map(e=>e.textContent)`)).some((text) =>
        text.includes("流式输出"),
      ),
    );
    console.log(
      "PASS renderer crash continues Agent; backend crash reconciles unknown outcome without replay",
    );
  }
  console.log(
    "PASS native Electron: isolated preload, real Runtime, stream, switch, IME, drafts, window reopen, stop, renderer reload and persisted Recents",
  );
} finally {
  if (app) await app.close();
  server.closeAllConnections();
  server.close();
  // Keep the isolated home for failed-run diagnosis; it never enters the repository.
  console.log("Disposable test home:", home);
}
