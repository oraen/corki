// Isolated cards contract. Run only against application_fixture.py.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
const base = process.env.CORKI_APPLICATION_TEST_URL;
if (!base?.startsWith("http://127.0.0.1:")) throw Error("Use the disposable application fixture");
const cli = (...args) => execFileSync("agent-browser", ["--session", "corki-app-cards", ...args], { encoding: "utf8" }).trim();
const evaluate = (script) => JSON.parse(cli("eval", script));
async function wait(script) {
  for (let n = 0; n < 60; n++) {
    if (evaluate(`Boolean(${script})`)) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw Error("Timeout: " + script);
}
cli("open", base + "/#applications");
await wait('document.querySelectorAll(".application-card").length===3');
assert(evaluate('document.querySelector(".application-my-apps > summary h2").textContent.startsWith("我的应用")'));
assert.equal(evaluate('document.querySelectorAll(".application-my-apps .application-card").length'),3);
assert(evaluate('["[data-app-search]", ".filters", ".application-create"].every(s=>document.querySelector(".application-my-apps").contains(document.querySelector(s)))'));
cli("click", "[data-app-search]");
assert(evaluate('document.querySelector(".application-my-apps").open'));
cli("click", ".application-my-apps > summary");
assert(!evaluate('document.querySelector(".application-my-apps").open'));
cli("click", '[data-application-kafka=refresh]');
assert(!evaluate('document.querySelector(".application-my-apps").open'));
cli("click", ".application-my-apps > summary");
cli("click", '[data-app-action=filter][data-kind=llm]');
await wait('document.querySelectorAll(".application-card").length===2');
assert(evaluate('document.querySelector(".application-my-apps").open'));
cli("click", '[data-app-action=filter][data-kind=""]');
await wait('document.querySelectorAll(".application-card").length===3');
assert(evaluate('document.querySelector(".application-my-apps").open'));
assert.equal(evaluate('document.querySelectorAll(".application-card .application-talk").length'), 0);
assert(evaluate('[...document.querySelectorAll(".application-card time")].every(t=>/^\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}$/.test(t.textContent))'));
assert.deepEqual(evaluate('[...document.querySelectorAll("[data-app-action=copy-app-id]")].map(b=>b.getAttribute("aria-label").split(" ").at(-1)).sort()'), ["100000", "100001", "100002"]);
// Clipboard API is substituted only to assert the exact copied value.
evaluate('Object.defineProperty(navigator,"clipboard",{configurable:true,value:{writeText:async text=>{window.copiedAppId=text;}}}); true');
const id = evaluate('document.querySelector(".application-card").dataset.cardId');
cli("click", '.application-card:first-child [data-app-action="copy-app-id"]');
assert.equal(evaluate('window.copiedAppId'), "100002");
assert.equal(evaluate('location.hash'), "#applications");
// Artificial network delay: only the selected switch becomes busy.
evaluate('window.cardOriginalFetch=window.fetch; window.fetch=async(...args)=>{if(args[1]?.body&&JSON.parse(args[1].body).action==="set-enabled")await new Promise(r=>setTimeout(r,1000));return window.cardOriginalFetch(...args)};true');
cli("click", '.application-card:first-child [role="switch"]');
assert.equal(evaluate('document.querySelectorAll(".application-card-switch:disabled").length'), 1);
assert.equal(evaluate('document.querySelectorAll(".application-card-switch:not(:disabled)").length'), 2);
await wait('document.querySelector(".application-card:first-child [role=switch]").getAttribute("aria-checked")==="false"&&!document.querySelector(".application-card-switch:disabled")');
evaluate('window.fetch=window.cardOriginalFetch;true');
cli("reload");
await wait(`document.querySelector('[data-card-id="${id}"] [role=switch]')?.getAttribute("aria-checked")==="false"`);
cli("set", "viewport", "1440", "900");
cli("screenshot", "/tmp/corki-application-cards-1440.png");
cli("set", "viewport", "1280", "800");
assert(evaluate('document.documentElement.scrollWidth<=window.innerWidth'));
cli("screenshot", "/tmp/corki-application-cards-1280.png");
cli("click", `[data-card-id="${id}"] .application-card-heading`);
await wait('document.querySelector(".application-detail-actions a")');
assert(evaluate('document.querySelector(".application-detail-actions a").hash.endsWith("/chat")'));
cli("open", base + "/#applications");
await wait(`document.querySelector('[data-card-id="${id}"] [role=switch]')`);
cli("click", `[data-card-id="${id}"] [role=switch]`);
await wait(`document.querySelector('[data-card-id="${id}"] [role=switch]').getAttribute("aria-checked")==="true"`);
console.log("Cards: IDs, copy, timestamp, toggle persistence, isolated busy state, detail chat, 1280/1440 layout passed");
