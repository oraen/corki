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
    if (!p.startsWith(root + "/")) throw Error();
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
const browser = await chromium.launch({
  executablePath:
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});
try {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 },
  });
  const failures = [];
  page.on("pageerror", (e) => failures.push(e.message));
  await page.addInitScript(() => {
    const state = new Map();
    window.__saved = state;
    window.__submissions = 0;
    let subscriber = () => {};
    const catalog = {
      valid: true,
      providers: [
        {
          id: "fixture",
          models: [
            {
              id: "fixture-model",
              reasoningLevels: [],
              reasoning: null,
              images: true,
            },
          ],
        },
      ],
      default: { provider: "fixture", model: "fixture-model", reasoning: null },
    };
    window.corki = {
      request: async (method, p) => {
        if (method === "initialize") return { instance: "test", sequence: 0, threadManagementVersion: 2 };
        if (method === "state.read") return state.get(p.key) || null;
        if (method === "state.write") {
          state.set(p.key, p.value);
          return {};
        }
        if (method === "config.read") return catalog;
        if (method === "threads.list") return { threads: [], next: null };
        if (method === "request.read") return window.__receipt || null;
        if (method === "turn.submit") {
          window.__lastSubmission = p;
          window.__submissions++;
          if (window.__loseReply) {
            window.__receipt = { id:p.requestId, thread_id:p.threadId, state:window.__loseReply };
            throw new Error("fixture acknowledgement lost");
          }
          if (window.__deferSubmission)
            await new Promise((resolve, reject) => {
              window.__accept = resolve;
              window.__reject = reject;
            });
          return { state: "accepted", thread_id: p.threadId };
        }
        if (method === "threads.read") {
          if(window.__deferReads) return await new Promise(resolve=>window.__reads.push(resolve));
          return {
            instance:"test", sequence:0,
            messages: [
              { id: "u", role: "user", content: "请介绍 Corki" },
              {
                id: "a",
                role: "assistant",
                content:
                  '# Corki\n\n简洁的 **Agent** 桌面。\n\n```python\nprint("hello")\n```',
              },
            ],
            turns: [],
            running: false,
            directory: "/tmp/workspace",
            next: null,
          };
        }
      },
      subscribe: (callback) => {
        subscriber = callback;
        window.__emit = callback;
        return () => {};
      },
      chooseDirectory: async () => "/tmp/workspace",
      chooseFiles: async () => [],
      importFiles: async () => [],
      copy: async (text) => {
        window.__copied = text;
      },
      openLink: async () => {},
      restartBackend: async () => {},
      configurePython: async () => true,
    };
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/index.html`);
  await page.getByRole("heading", { name: "Learning never ends" }).waitFor();
  await page.getByRole("button", { name: "选择模型", exact: true }).waitFor();
  await page.screenshot({ path: "/tmp/corki-desktop-empty.png" });
  await page
    .getByRole("textbox", { name: "消息", exact: true })
    .fill("请介绍 Corki");
  await page.getByRole("button", { name: "发送消息" }).click();
  await page.locator(".markdown h1").waitFor();
  await page.getByRole("button", { name: "复制回答原文" }).click();
  assert((await page.evaluate(() => window.__copied)).includes("**Agent**"));
  await page.screenshot({ path: "/tmp/corki-desktop-chat.png" });
  const originalText = await page.locator(".markdown").innerText();
  const selectedText = await page.evaluate(() => {
    const text = document.querySelector(".markdown h1").firstChild;
    const range = document.createRange();
    range.setStart(text, 0);
    range.setEnd(text, 2);
    const selection = document.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    window.__emit({
      instance: "test",
      sequence: 1,
      type: "AssistantTextDelta",
      threadId: window.__lastSubmission.threadId,
      data: { item_id: "a", delta: "\n\n追加内容" },
    });
    return selection.toString();
  });
  await page.waitForTimeout(100);
  assert.equal(await page.locator(".markdown").innerText(), originalText);
  assert.equal(
    await page.evaluate(() => document.getSelection().toString()),
    selectedText,
  );
  await page.evaluate(() => document.getSelection().removeAllRanges());
  await page.locator(".markdown").filter({ hasText: "追加内容" }).waitFor();
  await page.locator(".new-chat").click();
  const input = page.getByRole("textbox", { name: "消息", exact: true });
  await input.fill("first request");
  await page.evaluate(() => {
    window.__deferSubmission = true;
    window.__accept = null;
  });
  await page.getByRole("button", { name: "发送消息" }).click();
  await page.waitForFunction(() => typeof window.__accept === "function");
  assert.equal(await input.inputValue(), "");
  assert(await page.getByText("first request", {exact:true}).isVisible());
  assert.equal(await page.evaluate(() => window.__saved.get("draft:new")?.content), "first request");
  const count = await page.evaluate(() => window.__submissions);
  await input.fill("next draft must survive");
  await input.press("Enter");
  assert.equal(await page.evaluate(() => window.__submissions), count);
  await page.evaluate(() => window.__accept());
  await page.waitForFunction(
    () =>
      window.__saved.get("draft:" + window.__lastSubmission.threadId)
        ?.content === "next draft must survive",
  );
  assert.equal(await input.inputValue(), "next draft must survive");

  await page.locator(".new-chat").click();
  await input.fill("rejected request");
  await page.evaluate(() => {
    window.__accept = null;
    window.__reject = null;
  });
  await page.getByRole("button", { name: "发送消息" }).click();
  await page.waitForFunction(() => typeof window.__reject === "function");
  await input.fill("edited while waiting for rejection");
  await page.evaluate(() =>
    window.__reject(new Error("提交被拒绝： fixture rejection")),
  );
  await page
    .locator(".inline-error")
    .filter({ hasText: "fixture rejection" })
    .waitFor();
  assert.equal(await input.inputValue(), "edited while waiting for rejection");
  assert.equal(
    await page.getByRole("button", { name: "发送消息" }).isEnabled(),
    true,
  );
  await page.evaluate(() => { window.__deferSubmission=false; window.__loseReply='accepted'; });
  await input.fill("accepted but reply lost");
  const beforeLost = await page.evaluate(() => window.__submissions);
  await page.getByRole("button", {name:"发送消息"}).click();
  await page.waitForFunction(() => window.__saved.get('draft:'+window.__lastSubmission.threadId)?.content==='');
  assert.equal(await page.evaluate(() => window.__submissions), beforeLost+1);
  assert.equal(await input.inputValue(), '');
  assert.equal(await page.getByRole('button',{name:'核对状态'}).count(),0);

  await page.locator('.new-chat').click();
  await page.evaluate(() => { window.__loseReply='preparing'; window.__receipt=null; });
  await input.fill('uncertain submission must remain');
  await page.getByRole('button',{name:'发送消息'}).click();
  await page.getByRole('button',{name:'核对状态'}).waitFor();
  assert.equal(await input.inputValue(),'uncertain submission must remain');
  assert.equal(await page.getByRole('button',{name:'发送消息'}).isEnabled(),false);
  const uncertainCount=await page.evaluate(()=>window.__submissions);
  await page.getByRole('button',{name:'核对状态'}).click();
  assert.equal(await page.evaluate(()=>window.__submissions),uncertainCount);
  assert.equal(await input.inputValue(),'uncertain submission must remain');
  await page.evaluate(()=>window.__receipt.state='completed');
  await page.getByRole('button',{name:'核对状态'}).click();
  await page.waitForFunction(()=>window.__saved.get('draft:'+window.__lastSubmission.threadId)?.request===undefined && window.__saved.get('draft:'+window.__lastSubmission.threadId)?.content==='');
  assert.equal(await page.evaluate(()=>window.__submissions),uncertainCount);
  assert.equal(await input.inputValue(),'');
  await page.evaluate(()=>{
    window.__deferReads=true;window.__reads=[];
    for(const sequence of [2,3])window.__emit({type:'threads.changed',instance:'test',sequence,threadId:window.__lastSubmission.threadId,data:{}});
  });
  await page.waitForFunction(()=>window.__reads.length>=2);
  await page.evaluate(()=>{
    const snapshot={threadId:window.__lastSubmission.threadId,instance:'test',sequence:3,messages:[{id:'latest',role:'assistant',content:'Newest snapshot stays visible'}],turns:[],live:[],pending:[],running:false,directory:'/tmp/workspace',next:null};
    window.__reads.at(-1)(snapshot);
  });
  await page.locator('.markdown').filter({hasText:'Newest snapshot stays visible'}).waitFor();
  await page.evaluate(()=>{
    for(const resolve of window.__reads.slice(0,-1))resolve({threadId:window.__lastSubmission.threadId,instance:'test',sequence:2,messages:[{id:'stale',role:'assistant',content:'Stale snapshot must not replace newer content'}],turns:[],live:[],pending:[],running:false,directory:'/tmp/workspace',next:null});
    window.__deferReads=false;
  });
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  assert.equal(await page.locator('.markdown').filter({hasText:'Stale snapshot'}).count(),0);
  assert.equal(await page.locator('.markdown').filter({hasText:'Newest snapshot stays visible'}).count(),1);
  await page.evaluate(()=>{
    window.__deferReads=true;window.__reads=[];
    window.__emit({type:'threads.changed',instance:'test',sequence:4,threadId:window.__lastSubmission.threadId,data:{}});
  });
  await page.waitForFunction(()=>window.__reads.length===1);
  await page.evaluate(()=>{
    for(let sequence=5;sequence<=604;sequence++)window.__emit({type:'AssistantTextDelta',instance:'test',sequence,threadId:window.__lastSubmission.threadId,data:{item_id:'burst',delta:'x'}});
    window.__reads[0]({threadId:window.__lastSubmission.threadId,instance:'test',sequence:4,messages:[],turns:[],live:[],pending:[],running:true,directory:'/tmp/workspace',next:null});
  });
  await page.waitForFunction(()=>window.__reads.length===2);
  await page.evaluate(()=>{
    window.__reads[1]({threadId:window.__lastSubmission.threadId,instance:'test',sequence:610,messages:[{id:'burst',role:'assistant',content:'Complete resynchronized answer'}],turns:[],live:[],pending:[],running:false,directory:'/tmp/workspace',next:null});
  });
  await page.locator('.markdown').filter({hasText:'Complete resynchronized answer'}).waitFor();
  await page.evaluate(()=>window.__emit({type:'AssistantTextDelta',instance:'test',sequence:605,threadId:window.__lastSubmission.threadId,data:{item_id:'burst',delta:'ALREADY_INCLUDED'}}));
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  assert.equal(await page.locator('.markdown').filter({hasText:'ALREADY_INCLUDED'}).count(),0);
  assert.equal(await page.locator('.markdown').filter({hasText:'Complete resynchronized answer'}).count(),1);
  assert.deepEqual(failures, []);
  console.log(
    "PASS desktop layout, model selection, sending, Markdown, raw copy and streaming text selection, delayed receipts, draft preservation, lost acknowledgement reconciliation and no resubmission",
  );
} finally {
  await browser.close();
  server.close();
}
