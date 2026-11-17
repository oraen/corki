// Run against application_fixture.py only. Catalog mutations below are browser fixtures.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
const base = process.env.CORKI_APPLICATION_TEST_URL;
if (!base?.startsWith("http://127.0.0.1:")) throw Error("Disposable fixture URL required");
const cli = (...args) => execFileSync("agent-browser", ["--session", "corki-create-checks", ...args], { encoding: "utf8" }).trim();
const ev = (script) => JSON.parse(cli("eval", script));
const click = (selector) => cli("click", selector);
const fill = (selector, text) => cli("fill", selector, text);
const wait = async (script) => {
  for (let i = 0; i < 80; i++) {
    if (ev(`Boolean(${script})`)) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw Error("Timed out: " + script);
};
const snap = (name) => cli("screenshot", `/tmp/corki-create-${name}.png`);
const title = "创建页验收-" + Date.now();
cli("open", base + "/#applications/new");
ev('(()=>{const app=window.CorkiApplications;if(app){app.editing=false;app.drafts.delete("new");app.draftBases.delete("new");}localStorage.removeItem("corki.application.draft.new");return true;})()');
cli("reload");
await wait('document.querySelector(".app-create")');
ev(`(()=>{window.__saveCalls=0;window.__realFetch=window.fetch;window.fetch=async(...args)=>{if(args[1]?.method==='POST'&&JSON.parse(args[1].body).action==='save')window.__saveCalls++;return window.__realFetch(...args);};window.__createEditor=window.CorkiApplications.editor;return true;})()`);
assert.equal(ev('document.querySelector(".application-versions")'), null);
click('[data-app-action="save"]');
assert.equal(ev("window.__saveCalls"), 0);
assert.equal(ev('document.activeElement.name'), "name");
assert(ev('!document.querySelector("[data-create-error=name]").hidden'));
assert(ev('!document.querySelector("[data-create-error=prompt]").hidden'));
fill('[name="name"]', title);
fill('[name="description"]', "整理资料、解答问题\n协助完成日常工作。");
click('[data-app-tab=prompt]');
fill('.application-prompt .cm-content', "你是一位细致的文档助手。\n\n先理解用户的问题，再整理相关资料。\n\n信息不足时主动询问，回答简洁清楚。");
cli("set", "viewport", "1440", "900");
snap("llm-1440");
// A change with history must survive type/provider changes.
ev('(()=>{const editor=window.CorkiApplications.editor;editor.dispatch({changes:{from:editor.state.doc.length,insert:"补充说明"}});return true;})()');
click('[data-app-tab=info]');
click('.app-create-type-options label:has(input[value="agent"])');
assert(ev('window.__createEditor===window.CorkiApplications.editor'));
assert(ev('!document.querySelector("[data-create-agent]").hidden'));
fill('[name="directory"]', "/tmp/中文 工作目录");
click('[data-create-picker="provider"] summary');
click('[data-create-pick="provider"][data-value="local"]');
click('[data-create-picker="model"] summary');
click('[data-create-pick="model"][data-value="test"]');
assert(ev('window.__createEditor===window.CorkiApplications.editor'));
click('[data-app-tab=prompt]');
cli("focus", ".application-prompt .cm-content");
cli("press", "Meta+z");
assert(!ev('window.CorkiApplications.form.prompt.endsWith("补充说明")'));
fill('.application-prompt .cm-content', "你是一位细致的文档助手。\n\n先理解用户的问题，再整理相关资料。\n\n信息不足时主动询问，回答简洁清楚。");
click('[data-app-tab=info]');
click('[data-create-picker="permissions"] summary');
click('[data-create-pick="permissions"][data-value=":read-only"]');
click('[data-create-picker="permissions"] summary');
click('[data-create-pick="permissions"][data-value=":workspace"]');
assert.equal(ev('[...document.querySelectorAll("[data-create-picker=permissions] [role=option]")].filter(e=>e.textContent.includes("✓")).map(e=>e.dataset.value).join()'), ":workspace");
cli("focus", '[data-create-picker="provider"] summary');
cli("press", "ArrowUp");
assert.equal(ev('document.activeElement.dataset.value'), "local");
cli("press", "Escape");
assert(ev('document.activeElement.matches("[data-create-picker=provider] summary")'));
// Same name in two scopes must remain two distinct selections.
ev(`(()=>{const app=window.CorkiApplications;window.__originalCatalog=JSON.parse(JSON.stringify(app.catalog));app.catalog.skills=[
{id:'test-extension',name:'文档整理',group:'corki'},
{id:'test-public',name:'文档整理',group:'public'},
{id:'test-work',name:'extract-report',group:'work'},
{id:'test-builtin',name:'skill-creator',group:'builtin'},
{id:'test-long',name:'很长的技能名称'.repeat(12),group:'public'},
{id:'test-html',name:'<img src=x onerror=alert(1)>',group:'corki'}
];return true;})()`);
click('[data-create-action="skills"]');
assert.equal(ev('document.activeElement.dataset.createSkillSearch!==undefined'), true);
assert.equal(ev('document.querySelectorAll("[data-skill-scope]").length'), 5);
assert.equal(ev('document.querySelector(".app-create-skill-dialog img")'), null);
click('[data-skill-scope="corki"]');
click('[name="create-skill"][value="test-extension"]');
click('[data-skill-scope="public"]');
click('[name="create-skill"][value="test-public"]');
click('[data-skill-scope="all"]');
fill('[data-create-skill-search]', "extract-report");
click('[name="create-skill"][value="test-work"]');
fill('[data-create-skill-search]', "no-matching-name");
assert(ev('document.querySelector(".app-create-skill-no-results").textContent.includes("没有匹配")'));
fill('[data-create-skill-search]', "");
assert.equal(ev('document.querySelectorAll("[name=create-skill]:checked").length'), 3);
assert(ev('window.__createEditor===window.CorkiApplications.editor'));
snap("skills-selector");
cli("press", "Escape");
await wait('!document.querySelector(".app-create-skill-dialog")');
assert(ev('document.activeElement.matches("[data-create-action=skills]")'));
assert.equal(ev('document.querySelectorAll(".app-create-selected-skill").length'), 3);
assert.equal(ev('document.querySelectorAll(".app-create-selected-skill small")[0].textContent'), "扩展");
assert.equal(ev('document.querySelectorAll(".app-create-selected-skill small")[1].textContent'), "公共");
click('[data-create-action="remove-skill"][data-id="test-work"]');
assert.equal(ev('window.CorkiApplications.form.skills.length'), 2);
click('.app-create-type-options label:has(input[value="llm"])');
click('.app-create-type-options label:has(input[value="agent"])');
assert.equal(ev('document.querySelector("[name=directory]").value'), "/tmp/中文 工作目录");
assert.equal(ev('window.CorkiApplications.form.skills.length'), 2);
assert(ev('window.__createEditor===window.CorkiApplications.editor'));
for (const [width, height] of [[1280, 800], [1440, 900], [1920, 1080]]) {
  cli("set", "viewport", String(width), String(height));
  snap(`agent-${width}`);
  assert(ev("document.documentElement.scrollWidth<=innerWidth"));
  assert(ev('document.querySelector("[data-create-action=skills]").getBoundingClientRect().bottom<innerHeight'));
  assert(ev('document.querySelector("[data-app-tab=info]").getAttribute("aria-selected")==="true"'));
}
// Bound catalog rendering and enforce the existing backend maximum of100 selections.
ev(`(()=>{const app=window.CorkiApplications;app.catalog.skills=Array.from({length:1000},(_,i)=>({id:'large-'+i,name:'skill-'+i,group:['corki','public','work','builtin'][i%4]}));app.form.skills=[];app.createPage.renderSelected();return true;})()`);
click('[data-create-action="skills"]');
assert.equal(ev('document.querySelectorAll("[name=create-skill]").length'), 100);
click('[data-skill-more]');
assert.equal(ev('document.querySelectorAll("[name=create-skill]").length'), 200);
fill('[data-create-skill-search]', "skill-999");
click('[name="create-skill"][value="large-999"]');
fill('[data-create-skill-search]', "");
assert.equal(ev('window.CorkiApplications.form.skills[0]'), "large-999");
cli("press", "Escape");
await wait('!document.querySelector(".app-create-skill-dialog")');
ev('(()=>{const app=window.CorkiApplications;app.form.skills=app.catalog.skills.slice(0,100).map(s=>s.id);app.createPage.renderSelected();return true;})()');
click('[data-create-action="skills"]');
fill('[data-create-skill-search]', "skill-999");
click('[name="create-skill"][value="large-999"]');
assert.equal(ev('window.CorkiApplications.form.skills.length'), 100);
assert(ev('!document.querySelector("[data-skill-limit-error]").hidden'));
cli("press", "Escape");
await wait('!document.querySelector(".app-create-skill-dialog")');
// Existing definitions remain untouched by a catalog becoming empty or invalid.
ev('(()=>{const app=window.CorkiApplications;app.catalog={...app.catalog,providers:[],default:{},skills:[],error:"配置无效，请修复后重试"};app.form.provider="";app.form.model="";app.form.skills=[];app.detail();return true;})()');
assert(ev('!document.querySelector(".app-create-model-note").hidden'));
click('[data-create-action="skills"]');
assert(ev('document.querySelector(".app-create-skill-no-results").textContent.includes("暂无可用")'));
snap("no-skills");
cli("press", "Escape");
await wait('!document.querySelector(".app-create-skill-dialog")');
ev('(()=>{const app=window.CorkiApplications;app.catalog=window.__originalCatalog;app.form.skills=[];app.detail();return true;})()');
click('.app-create-type-options label:has(input[value="llm"])');
// Draft survives a full reload, including multiline description and hidden Agent settings.
cli("reload");
await wait('document.querySelector(".app-create")');
assert.equal(ev('document.querySelector("[name=name]").value'), title);
assert(ev('document.querySelector("[name=description]").value.split(String.fromCharCode(10)).length>1'));
assert(ev('document.querySelector("[data-create-agent]").hidden'));
assert.equal(ev('window.CorkiApplications.form.directory'), "/tmp/中文 工作目录");
// A rejected request retains content and enables a deliberate retry.
ev(`(()=>{window.__restoreFetch=window.fetch;window.fetch=async(...args)=>{if(args[1]?.method==='POST'&&JSON.parse(args[1].body).action==='save')return new Response(JSON.stringify({error:'临时保存失败，请重试'}),{status:503,headers:{'Content-Type':'application/json'}});return window.__restoreFetch(...args);};return true;})()`);
click('[data-app-action="save"]');
await wait('!document.querySelector(".app-create-save-error").hidden');
assert.equal(ev('document.querySelector("[name=name]").value'), title);
assert(!ev('document.querySelector(".application-definition").inert'));
assert(!ev('document.querySelector("[data-app-action=save]").disabled'));
snap("save-error");
ev('(()=>{window.fetch=window.__restoreFetch;delete window.__restoreFetch;return true;})()');
fill('[name="name"]', "写作助手");
click('[data-app-action="save"]');
await wait('document.querySelector("[data-create-error=name]").textContent.includes("已存在")');
fill('[name="name"]', title);
ev(`(()=>{window.__postCount=0;window.__restoreFetch=window.fetch;window.fetch=async(...args)=>{if(args[1]?.method==='POST'&&JSON.parse(args[1].body).action==='save'){window.__postCount++;const response=await window.__restoreFetch(...args);await new Promise(r=>setTimeout(r,1000));return response;}return window.__restoreFetch(...args);};return true;})()`);
click('[data-app-action="save"]');
assert(ev('document.querySelector(".application-definition").inert'));
assert(ev('document.querySelector("[data-app-action=save]").disabled'));
ev('(()=>{document.querySelector("[data-app-action=save]").click();return true;})()');
snap("saving");
await wait('document.querySelector("[data-app-action=edit]")');
assert.equal(ev("window.__postCount"), 1);
const id = ev('location.hash.split("/")[1]');
assert(id !== "new");
assert.equal(ev('document.querySelectorAll("[data-app-action=version]").length'), 1);
await fetch(base + "/api/applications", {method:"POST",headers:{"Content-Type":"application/json","X-Corki-Manager":"1"},body:JSON.stringify({action:"delete",app:id,stop:true})});
assert.equal(cli("errors"), "");
console.log("PASS: creation validation, stable editor/undo, type/model/permission changes, four Skills scopes, duplicate names, filtering/removal/limits, three viewports, no configuration/Skills, multiline drafts, rejected saves, duplicate name and single accepted submission.");
