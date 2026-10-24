import { browserLaunchOptions } from './platform.mjs';
import { chromium } from "playwright";
import http from "node:http";
import { readFile, mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import assert from "node:assert/strict";
const root = path.resolve("dist");
const server = http.createServer(async (req, res) => {
  try {
    const p = path.resolve(
      root,
      "." + new URL(req.url, "http://localhost").pathname,
    );
    if (!p.startsWith(root + path.sep)) throw Error();
    const body = await readFile(p);
    res.setHeader(
      "Content-Type",
      p.endsWith(".html")
        ? "text/html"
        : p.endsWith(".js")
          ? "text/javascript"
          : p.endsWith(".css")
            ? "text/css"
            : p.endsWith(".png")
              ? "image/png"
              : "image/jpeg",
    );
    res.end(body);
  } catch {
    res.writeHead(404).end();
  }
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const browser = await chromium.launch(browserLaunchOptions);
try {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 },
  });
  const failures = [];
  page.on("pageerror", (e) => failures.push(e.message));
  await page.addInitScript(() => {
    const state = new Map([["ui", { selected: "thread" }]]);
    window.seq = 0;
    window.emit = (type, data) =>
      window.listener({
        instance: "fixture",
        sequence: ++window.seq,
        threadId: "thread",
        type,
        data,
      });
    window.corki = {
      request: async (method, p) => {
        if (method === "initialize")
          return {
            instance: "fixture",
            sequence: 0,
            threadManagementVersion: 2,
          };
        if (method === "state.read") return state.get(p.key) || null;
        if (method === "state.write") {
          state.set(p.key, p.value);
          return {};
        }
        if (method === "config.read")
          return {
            valid: true,
            default: { provider: "fixture", model: "fixture" },
            providers: [
              {
                id: "fixture",
                models: [{ id: "fixture", reasoningLevels: [], images: false }],
              },
            ],
          };
        if (method === "threads.list")
          return {
            threads: [
              {
                id: "thread",
                title: "公开数据查询",
                cwd: "/tmp/fixture",
                running: true,
              },
            ],
            next: null,
            directories: [],
          };
        if (method === "threads.read")
          return {
            threadId: "thread",
            instance: "fixture",
            sequence: window.seq,
            messages: [
              { id: "u1", role: "user", content: "上一次测试", turnId: "old", createdAt: "2026-10-17T05:00:00Z" },
              {
                id: "error:old",
                role: "error",
                content: "synthetic HTTP 400",
                turnId: "old",
              },
              {
                id: "u2",
                role: "user",
                content: "请查询公开统计数据",
                turnId: "current",
              },
            ],
            next: null,
            turns: [],
            live: [],
            pending: [],
            running: true,
            directory: "/tmp/fixture",
          };
      },
      subscribe: (fn) => {
        window.listener = fn;
        return () => {};
      },
      copy: async (text) => (window.copied = text),
      openLink: async () => {},
    };
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/index.html`);
  await page.locator(".thinking-indicator").waitFor();
  assert.match(await page.locator('.message.user time').first().innerText(), /^2026-10-17 \d{2}:00$/);
  assert.equal(await page.locator('.user-message-meta .message-fold').count(), 0);
  assert.equal(await page.locator('.user-message-meta time').count(), 1);

  assert.equal(await page.getByRole("button", {name:"最近会话", exact:true}).locator(":scope > svg").count(), 1);
  assert.equal(await page.locator(".message-error").count(), 1);
  await page.evaluate(() => {
    window.emit("AssistantTextDelta", {
      turn_id: "current",
      item_id: "progress",
      delta: "搜索接口限流了，我改查公开统计页面。",
    });
    window.emit("AssistantMessageCompleted", {
      turn_id: "current",
      item_id: "progress",
      text: "搜索接口限流了，我改查公开统计页面。",
    });
    window.emit("ToolCallStarted", {
      turn_id: "current",
      tool_call_id: "c",
      tool_name: "exec_command",
      arguments_preview: JSON.stringify({
        cmd: 'python -c "print(123)" ' + "long-command ".repeat(15),
        workdir: "/tmp/fixture",
        timeout: 30,
      }),
    });
  });
  await page.locator(".tool-activity").waitFor();
  await page.locator(".thinking-indicator").waitFor();
  assert.match(await page.locator('.message.user time').first().innerText(), /^2026-10-17 \d{2}:00$/);
  assert.equal(await page.locator('.user-message-meta .message-fold').count(), 0);
  assert.equal(await page.locator('.user-message-meta time').count(), 1);

  assert.equal(await page.locator(".agent-progress").count(), 1);
  assert.equal(await page.locator('.thinking-indicator img').count(), 0);
  assert.equal(await page.locator('.process-heading img[alt="Corki"]').count(), 1);
  assert(await page.locator('.process-heading').evaluate(n=>Math.abs(n.querySelector('img').getBoundingClientRect().top - n.getBoundingClientRect().top)<2));
  assert.equal(await page.locator(".message.assistant").count(), 0);
  assert(
    (await page.locator(".tool-activity summary").innerText()).includes(
      "python -c",
    ),
  );
  assert(
    await page
      .locator(".activity-title")
      .evaluate((n) => n.scrollWidth > n.clientWidth),
  );
  await page.evaluate(() => {
    window.emit("ToolOutputDelta", {
      turn_id: "current",
      tool_call_id: "c",
      delta: JSON.stringify({
        ok: true,
        count: 123,
        content: "<script>window.injected=true</script>",
      }),
    });
    window.emit("ToolCallCompleted", {
      turn_id: "current",
      tool_call_id: "c",
      is_error: false,
      elapsedSeconds: 7401,
    });
  });
  await page.locator(".tool-activity summary").click();
  await page.locator(".tool-sections .hljs-number").first().waitFor();
  assert.equal(await page.locator(".tool-activity").count(), 1);
  assert.equal(await page.locator(".tool-sections section").count(), 2);
  assert.equal(await page.locator(".execution-duration").innerText(), "（耗时2h 3m 21s）");
  assert.equal(await page.evaluate(() => window.injected), undefined);
  await page.getByRole("button", { name: "复制执行输出", exact: true }).click();
  assert.equal(JSON.parse(await page.evaluate(() => window.copied)).count, 123);
  await page.evaluate(() => {
    window.emit("AssistantTextDelta", {
      turn_id: "current",
      item_id: "final",
      delta: "## 查询结果\n\n已获得公开统计数据。",
    });
    window.emit("AssistantMessageCompleted", {
      turn_id: "current",
      item_id: "final",
      text: "## 查询结果\n\n已获得公开统计数据。",
    });
    window.emit("TurnCompleted", { turn_id: "current" });
  });
  await page.getByRole("heading", { name: "查询结果" }).waitFor();
  assert.equal(await page.locator(".thinking-indicator").count(), 0);
  assert.equal(await page.locator(".message.assistant").count(), 1);
  assert.equal(await page.locator('.conversation-inner img[alt="Corki"]').count(), 1);
  assert.equal(await page.locator('.process-heading img[alt="Corki"]').count(), 1);
  await page.evaluate(()=>window.emit('TurnCancelled',{turn_id:'current'}));
  await page.locator('.message-stopped').waitFor();
  assert.equal(await page.locator('.process-heading img[alt="Corki"]').count(), 1);
  assert.equal(await page.locator('.thinking-indicator').count(), 0);
  const order = await page.locator(".conversation-inner").innerText();
  assert(
    order.indexOf("synthetic HTTP 400") < order.indexOf("请查询公开统计数据"),
  );
  assert.deepEqual(failures, []);
  await page.screenshot({ path: path.join(os.tmpdir(), 'corki-transcript-1440.png') });
  await page.setViewportSize({ width: 1280, height: 800 });
  assert(
    await page
      .locator(".tool-activity")
      .evaluate((n) => n.getBoundingClientRect().right <= window.innerWidth),
  );
  await page.screenshot({ path: path.join(os.tmpdir(), 'corki-transcript-1280.png') });
  // Explicitly opened child remains visible when the turn finishes.
  assert(await page.locator('.process-group').evaluate(n=>n.open));
  // Both fixture questions fit on a single rendered line.
  assert.equal(await page.getByRole('button',{name:'收起提问',exact:true}).count(),0);
  assert(await page.getByRole('button',{name:'收起回答',exact:true}).evaluate(button=>{
    const heading=button.closest('.reply-block').querySelector('.process-heading').getBoundingClientRect();
    const box=button.getBoundingClientRect();
    return box.top>=heading.top && box.bottom<=heading.bottom+1;
  }));
  await page.getByRole('button',{name:'收起回答',exact:true}).click();
  assert.equal(await page.locator('.message.assistant .markdown:visible').count(),0);
  await page.getByRole('button',{name:'展开回答',exact:true}).click();
  await page.getByRole('heading',{name:'查询结果'}).waitFor();
  await page.reload();
  await page.locator('.thinking-indicator').waitFor();
  await page.evaluate(()=>{
    window.emit('AssistantMessageCompleted',{turn_id:'current',item_id:'p',text:'正在查询'});
    window.emit('ToolCallStarted',{turn_id:'current',tool_call_id:'c',tool_name:'exec_command',arguments_preview:'{"cmd":"pwd"}'});
  });
  await page.locator('.process-group').waitFor();
  assert(await page.locator('.process-group').evaluate(n=>n.open));
  await page.evaluate(()=>{
    window.emit('AssistantMessageCompleted',{turn_id:'current',item_id:'final',text:'查询完成',createdAt:'2026-10-17T05:01:00Z'});
    window.emit('ToolCallCompleted',{turn_id:'current',tool_call_id:'c',is_error:false});
    window.emit('TurnCompleted',{turn_id:'current'});
  });
  await page.waitForFunction(()=>!document.querySelector('.process-group').open);
  assert.match(await page.locator('.message.assistant time').last().innerText(), /^2026-10-17 \d{2}:01$/);
  assert(await page.locator('.assistant-message-meta').last().evaluate(n => {
    const a=n.querySelector('time').getBoundingClientRect(), b=n.querySelector('.copy-button').getBoundingClientRect();
    return Math.abs((a.top+a.bottom)-(b.top+b.bottom))<3;
  }));

  assert.equal(await page.locator('.tool-activity:visible').count(),0);
  await page.screenshot({path:path.join(os.tmpdir(), 'corki-process-folded.png')});
  await page.locator('.process-heading img[alt="Corki"]').click();
  assert.equal(await page.locator('.process-group').evaluate(n=>n.open),false);
  await page.locator('.process-heading').click();
  await page.locator('.process-heading img[alt="Corki"]').click();
  assert.equal(await page.locator('.process-group').evaluate(n=>n.open),true);
  assert.equal(await page.locator('.tool-activity:visible').count(),1);
  assert.equal(await page.locator('.process-heading img[alt="Corki"]').count(),1);
  assert.equal(await page.locator('.process-body img[alt="Corki"]').count(),0);
  console.log(
    "Transcript UI passed: turn errors, thinking, commentary, paired tools, highlight, copy, layout",
  );
} finally {
  await browser.close();
  server.close();
}
