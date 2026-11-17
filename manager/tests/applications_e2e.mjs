// Run only against application_fixture.py, never the user's Manager.
import { execFileSync } from "node:child_process";
import assert from "node:assert/strict";
const base = process.env.CORKI_APPLICATION_TEST_URL;
if (!base?.startsWith("http://127.0.0.1:"))
  throw Error("Set CORKI_APPLICATION_TEST_URL to the disposable fixture");
const session = "corki-applications-qa";
const cli = (...args) =>
  execFileSync("agent-browser", ["--session", session, ...args], {
    encoding: "utf8",
  }).trim();
const evaluate = (expression) => JSON.parse(cli("eval", expression));
const wait = async (expression) => {
  for (let i = 0; i < 80; i++) {
    if (evaluate(`Boolean(${expression})`)) return;
    await new Promise((r) => setTimeout(r, 100));
  }
  throw Error("Timeout: " + expression);
};
const click = (selector) => cli("click", selector);
const fill = (selector, value) => cli("fill", selector, value);
const screenshot = (name) =>
  cli("screenshot", "/tmp/corki-applications-" + name + ".png");
cli("open", base + "/#applications/new");
cli("reload");
await wait('document.querySelector("input[name=name]")');
fill("input[name=name]", "浏览器验收应用-" + Date.now());
fill("textarea[name=description]", "临时测试\n不连接真实模型");
click('[data-app-tab="prompt"]');
fill(".application-prompt .cm-content", "请简洁地回答问题。");
click('[data-app-tab="info"]');
assert.equal(evaluate('document.querySelector(".application-versions")'), null);
assert.equal(evaluate('document.querySelector("[data-app-action=save]").textContent'), "创建应用");
const draftName = evaluate('document.querySelector("input[name=name]").value');
click('.app-create-type-options label:has(input[value="agent"])');
assert(evaluate('Boolean(document.querySelector("input[name=directory]"))'));
assert.equal(evaluate('document.querySelector("input[name=name]").value'), draftName);
assert(evaluate('document.querySelector(".application-prompt").textContent.includes("请简洁地回答问题。")'));
assert(evaluate('document.querySelector("textarea[name=description]").rows>=2'));
assert(evaluate('document.querySelectorAll("[data-app-tab]").length===2'));
assert(evaluate('document.querySelector(".application-create-page").getBoundingClientRect().width>=document.querySelector(".applications-workspace").clientWidth-2'));
assert(evaluate('!document.querySelector(".application-editor-status")&&!document.querySelector(".applications-workspace").textContent.includes("编辑中")'));
cli("focus", '[name=application-type][value=agent]');
// Native radio arrow navigation survives the conditional form rebuild.
cli("press", "ArrowLeft");
assert(evaluate('document.querySelector("[name=application-type][value=llm]").checked'));
assert(evaluate('Boolean(document.querySelector("input[name=context]"))'));
assert(evaluate('document.querySelector("[data-create-agent]").hidden'));
cli("reload");
await wait('document.querySelector("input[name=name]")');
assert.equal(evaluate('document.querySelector("textarea[name=description]").value'), "临时测试\n不连接真实模型");
assert.equal(evaluate('document.querySelector("input[name=name]").value'), draftName);
assert(evaluate('document.querySelector(".application-prompt").textContent.includes("请简洁地回答问题。")'));
cli("set", "viewport", "1536", "1024");
screenshot("create-refined");
cli("set", "viewport", "1280", "800");
assert(evaluate("document.documentElement.scrollWidth<=window.innerWidth"));
screenshot("create-refined-1280");
cli("set", "viewport", "1536", "1024");
click("[data-app-action=save]");
await wait('document.querySelector("[data-app-action=edit]")');
const id = evaluate('location.hash.split("/")[1]');
assert(id && id !== "new");
click('[data-app-tab="prompt"]');
click("[data-app-action=edit]");
fill(".application-prompt .cm-content", "请先给结论，再简洁说明理由。");
click("[data-app-action=save]");
await wait('document.querySelectorAll("[data-app-action=version]").length===2');
click('[data-app-action=version][data-version="1"]');
await wait('document.querySelector("[data-app-action=edit]").disabled');
assert(
  evaluate(
    'document.querySelector(".application-prompt").textContent.includes("请简洁地回答问题。")',
  ),
);
click('[data-app-action=diff][data-version="1"]');
await wait('document.querySelector(".cm-mergeView")');
assert(
  evaluate('document.querySelector(".header-back").getAttribute("href")') ===
    "#applications/" + id,
);
screenshot("diff");
click("[data-app-action=back-detail]");
await wait(
  `document.querySelector('[data-app-action=restore][data-version="1"]')`,
);
click('[data-app-action=restore][data-version="1"]');
click(".application-confirm [data-confirm]");
await wait('document.querySelectorAll("[data-app-action=version]").length===3');
click(".application-detail-actions a");
await wait(
  'document.querySelector("[data-app-input]")&&!document.querySelector("[data-app-action=send]").disabled',
);
fill("[data-app-input]", "你好，验证流式与历史");
evaluate(
  `(()=>{window.__originalAppFetch=window.fetch;let dropped=false;window.fetch=async(...args)=>{const response=await window.__originalAppFetch(...args);if(!dropped&&args[1]?.method==='POST'&&JSON.parse(args[1].body).action==='send'){dropped=true;throw new TypeError('fixture receipt lost');}return response;};return true;})()`,
);
click("[data-app-action=send]");
await wait('document.querySelector("[data-run]")');
evaluate(
  "(()=>{window.fetch=window.__originalAppFetch;delete window.__originalAppFetch;return true;})()",
);
const firstSession = evaluate("window.CorkiApplications.chatState.session");
cli("open", base + "/#overview");
cli("open", base + "/#applications/" + id + "/chat");
await wait(
  'document.querySelector("[data-answer]")?.textContent.includes("应用已接通")&&!document.querySelector("[data-app-action=send]").hidden',
);
assert.equal(
  evaluate("window.CorkiApplications.chatState.session"),
  firstSession,
);
assert(
  evaluate('document.querySelector("[data-user-text]").textContent') ===
    "你好，验证流式与历史",
);
assert(evaluate('document.querySelector(".tok-bool")?.textContent') === "true");
screenshot("chat-final");
fill("[data-app-input]", "验证停止");
click("[data-app-action=send]");
await wait('!document.querySelector("[data-app-action=stop]").hidden');
click("[data-app-action=stop]");
await wait(
  'document.querySelector(".application-messages").textContent.includes("回答已停止")',
);
fill("[data-app-input]", "第一会话草稿");
click("[data-app-action=new-session]");
assert.equal(evaluate('document.querySelector("[data-app-input]").value'), "");
fill("[data-app-input]", "新会话草稿");
click('[data-app-action=session][data-id="' + firstSession + '"]');
await wait('document.querySelector("[data-app-input]").value==="第一会话草稿"');
click(".application-session.selected summary");
click(".application-session.selected [data-app-action=rename-session]");
fill(".application-confirm input", "已改名的会话");
click(".application-confirm .primary");
await wait(
  'document.querySelector("[data-app-sessions]").textContent.includes("已改名的会话")',
);
click(".application-session.selected summary");
click(".application-session.selected [data-app-action=delete-session]");
click(".application-confirm [data-confirm]");
await wait('!document.querySelector("[data-run]")');
cli("open", base + "/#applications");
await wait(
  `document.querySelector('[data-app-action=delete][data-id="${id}"]')`,
);
click('[data-app-action=delete][data-id="' + id + '"]');
click(".application-confirm [data-confirm]");
await wait(
  `!document.querySelector('[data-app-action=delete][data-id="${id}"]')`,
);
const agentLink = evaluate(
  `[...document.querySelectorAll('.application-card')].find(c=>c.querySelector('h2').textContent==='代码助手').querySelector('.application-card-heading').href`,
);
cli("open", agentLink);
await wait('document.querySelector(".application-detail-actions a")');
click(".application-detail-actions a");
await wait(
  'document.querySelector("[data-app-input]")&&!document.querySelector("[data-app-action=send]").disabled',
);
click("[data-app-action=new-session]");
fill("[data-app-input]", "验证 Agent 应用的真实运行时");
click("[data-app-action=send]");
await wait('document.querySelector("[data-run]")');
cli("reload");
await wait(
  'document.querySelector("[data-answer]")?.textContent.includes("应用已接通")&&!document.querySelector("[data-app-action=send]").hidden',
);
assert.equal(
  evaluate('document.querySelectorAll(".application-turn").length'),
  1,
);
assert.equal(
  evaluate('document.querySelector("[data-user-text]").textContent'),
  "验证 Agent 应用的真实运行时",
);
cli("set", "viewport", "1280", "800");
screenshot("agent-1280");
assert(evaluate("document.documentElement.scrollWidth<=window.innerWidth"));
cli("set", "viewport", "1536", "1024");
const errors = cli("errors");
assert.equal(errors, "");
console.log(
  "PASS: create/edit, historical selection, diff/back, restore, streaming, navigation recovery, Markdown highlighting, stop, per-session drafts, rename and physical deletion.",
);
