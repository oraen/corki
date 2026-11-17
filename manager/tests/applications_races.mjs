// Deliberately delayed HTTP replies must never overwrite a different application.
import { execFileSync } from "node:child_process";
import assert from "node:assert/strict";
const base = process.env.CORKI_APPLICATION_TEST_URL;
if (!base?.startsWith("http://127.0.0.1:"))
  throw Error("Disposable fixture URL required");
const cli = (...args) =>
  execFileSync(
    "agent-browser",
    ["--session", "corki-applications-qa", ...args],
    { encoding: "utf8" },
  ).trim();
const ev = (expression) => JSON.parse(cli("eval", expression));
const wait = async (expression) => {
  for (let i = 0; i < 60; i++) {
    if (ev(`Boolean(${expression})`)) return;
    await new Promise((r) => setTimeout(r, 100));
  }
  throw Error("Timeout " + expression);
};
cli("open", base + "/#applications");
cli("reload");
await wait('document.querySelectorAll(".application-card").length>=3');
const links = ev(
  `Object.fromEntries([...document.querySelectorAll('.application-card')].map(c=>[c.querySelector('h2').textContent,c.querySelector('.application-card-main').href]))`,
);
cli("open", links["写作助手"]);
await wait('document.querySelector("[data-app-action=edit]")');
cli("click", "[data-app-action=edit]");
cli("fill", "textarea[name=description]", "延迟保存回复的独立测试");
cli("click", '[data-app-tab="prompt"]');
cli("fill", ".application-prompt .cm-content", "用于历史选择竞态验证的新提示词。");
ev(
  `(() => {window.__fixtureFetch=window.fetch;window.fetch=async(...args)=>{const r=await window.__fixtureFetch(...args);if(args[1]?.method==='POST'&&JSON.parse(args[1].body).action==='save')await new Promise(done=>setTimeout(done,600));return r;};return true;})()`,
);
cli("click", "[data-app-action=save]");
cli("open", links["数据解读"]);
await wait(
  'document.querySelector(".application-identity h2")?.textContent==="数据解读"',
);
assert.equal(
  ev(
    `new Promise(done=>setTimeout(()=>done(document.querySelector('.application-identity h2')?.textContent),800))`,
  ),
  "数据解读",
);
assert.notEqual(
  ev('document.querySelector("textarea[name=description]").value'),
  "延迟保存回复的独立测试",
);
ev("(() => {window.fetch=window.__fixtureFetch;return true;})()");
cli("open", links["写作助手"]);
await wait('document.querySelectorAll("[data-app-action=version]").length>=2');
const current = ev("window.CorkiApplications.app.version");
cli("click", '[data-app-tab="prompt"]');
ev(
  `(() => {window.__fixtureFetch=window.fetch;window.fetch=async(...args)=>{const r=await window.__fixtureFetch(...args);if(typeof args[0]==='string'&&args[0].includes('version=1'))await new Promise(done=>setTimeout(done,600));return r;};return true;})()`,
);
cli("click", '[data-app-action=version][data-version="1"]');
cli("click", `[data-app-action=version][data-version="${current}"]`);
assert.equal(
  ev(
    "new Promise(done=>setTimeout(()=>done(window.CorkiApplications.historical),800))",
  ),
  null,
);
ev(
  "(() => {window.fetch=window.__fixtureFetch;delete window.__fixtureFetch;return true;})()",
);
// Completing a historical read must not pull the user back from another tab.
ev(`(()=>{window.__fixtureFetch=window.fetch;window.fetch=async(...args)=>{const response=await window.__fixtureFetch(...args);if(typeof args[0]==='string'&&args[0].includes('version=1'))await new Promise(resolve=>setTimeout(resolve,600));return response;};return true;})()`);
cli("click", '[data-app-action=version][data-version="1"]');
cli("click", '[data-app-tab="info"]');
assert.equal(ev("new Promise(resolve=>setTimeout(()=>resolve(window.CorkiApplications.detailTab),800))"),"info");
assert(ev('document.querySelector("[data-app-tab=info]").getAttribute("aria-selected")==="true"'));
ev("(()=>{window.fetch=window.__fixtureFetch;delete window.__fixtureFetch;return true;})()");
// A restored draft retains its original revision instead of silently rebasing.
cli("click", '[data-app-tab="info"]');
cli("click", "[data-app-action=edit]");
cli("fill", "textarea[name=description]", "保留的本地草稿");
const appId = ev("window.CorkiApplications.id");
cli("open", links["数据解读"]);
await wait(
  'document.querySelector(".application-identity h2")?.textContent==="数据解读"',
);
const remote = await (
  await fetch(
    base +
      "/api/applications?" +
      new URLSearchParams({ action: "detail", app: appId }),
  )
).json();
const fieldNames = [
  "name",
  "description",
  "type",
  "provider",
  "model",
  "prompt",
  "context",
  "directory",
  "permissions",
  "skills",
];
const changed = Object.fromEntries(fieldNames.map((k) => [k, remote[k]]));
changed.description = "外部页面的更新";
const update = await fetch(base + "/api/applications", {
  method: "POST",
  headers: { "Content-Type": "application/json", "X-Corki-Manager": "1" },
  body: JSON.stringify({
    action: "save",
    app: appId,
    expected: remote.version,
    values: changed,
  }),
});
assert(update.ok);
cli("open", links["写作助手"]);
await wait(
  'document.querySelector("textarea[name=description]")?.value==="保留的本地草稿"',
);
cli("click", "[data-app-action=save]");
await wait(
  'document.querySelector("#toast").textContent.includes("其他页面更新")',
);
assert.equal(
  ev('document.querySelector("textarea[name=description]").value'),
  "保留的本地草稿",
);
cli("click", "[data-app-action=cancel-edit]");
assert.equal(cli("errors"), "");
console.log(
  "PASS: delayed save cannot overwrite another application; stale historical reads cannot replace a newer selection.",
);
