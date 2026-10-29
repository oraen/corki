// Targeted UI coverage. Requires a disposable manager; never uses production data.
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
const base = process.env.MANAGER_TEST_URL;
assert(base, "Set a disposable MANAGER_TEST_URL");
const config = await (await fetch(base + "/api/config")).json();
assert(
  /channel.*(?:qa|research)|gateway.*qa/.test(config.path),
  "Disposable gateway test home required",
);
const before = config.revision,
  exec = promisify(execFile),
  session = "corki-gateway-ui-" + process.pid;
const browser = (...args) =>
  exec("agent-browser", ["--session", session, ...args], {
    maxBuffer: 1024 * 1024,
  });
const wait = (expression) =>
  browser(
    "eval",
    `(async()=>{for(let i=0;i<200;i++){if(${expression})return true;await new Promise(r=>setTimeout(r,30))}throw Error('Gateway UI condition failed')})()`,
  );
try {
  await browser("open", base + "/#overview");
  await wait(`document.querySelector('[data-action="open-channels"]')`);
  await browser("click", '[data-action="open-channels"]');
  await wait(`document.querySelectorAll('.channel-card').length>=5`);
  await wait(`document.querySelectorAll('.channel-card .channel-avatar img').length===5 && [...document.querySelectorAll('.channel-card .channel-avatar img')].every(i=>i.complete && i.naturalWidth>0)`);
  const gateway = await (await fetch(base + '/api/channel-gateway')).json();
  if (gateway.installed.telegram) {
    await browser("click",'[data-uninstall="telegram"]');
    await browser("click",'.channel-dialog [type="submit"]');
    await wait(`!document.querySelector('.channel-dialog') && document.querySelector('[data-install="telegram"]') && !document.querySelector('[data-install="telegram"]').disabled`);
  }
  const geometry=JSON.parse((await browser("eval",`JSON.stringify((()=>{
    const button=document.querySelector('[data-install="telegram"]');
    const before=button.closest('.channel-card').getBoundingClientRect().height;
    button.click();
    const next=document.querySelector('[data-install="telegram"]');
    const card=next.closest('.channel-card');
    return {before,after:card.getBoundingClientRect().height,label:next.textContent,
      pulse:card.classList.contains('is-processing'),progress:!!card.querySelector('[role="progressbar"]')};
  })())`)).stdout.trim());
  const measured=typeof geometry==="string"?JSON.parse(geometry):geometry;
  assert.equal(measured.before,measured.after);
  assert.equal(measured.label,"安装中");
  assert.equal(measured.pulse,true);
  assert.equal(measured.progress,false);
  await wait(`document.querySelector('[data-uninstall="telegram"]') && !document.querySelector('[data-uninstall="telegram"]').disabled`);
  const created = [];
  const nonce = Date.now();
  const firstName = "UI account one " + nonce, secondName = "UI account two " + nonce;
  
  for (const name of [firstName,secondName]) {
    await browser("click", '[data-act="add-connection"]');
    await browser("click", ".channel-select summary");
    await browser("click", '[data-option="telegram"]');
    await browser("click", ".channel-dialog-foot .primary");
    await browser("fill", '[name="_name"]', name);
    await browser("fill", '[name="token"]', "fixture-not-a-credential");
    await browser("click", ".channel-reveal");
    await wait(`document.querySelector('[name="token"]').type==='text'`);
    await browser("click", '.channel-dialog [type="submit"]');
    await wait(`!document.querySelector('.channel-dialog') && document.querySelector('.channel-connections').textContent.includes('${name}')`);
    const snapshot = await (await fetch(base + "/api/channel-gateway")).json();
    created.push(snapshot.connections.find(c => c.name === name));
  }
  assert.notEqual(created[0].conversation_id,created[1].conversation_id);
  assert.notEqual(created[0].id,created[1].id);
  await browser("click",'[data-config="'+created[0].id+'"]');
  await wait(`document.querySelector('.channel-management') && document.querySelector('[name="_name"]')`);
  await browser("click",'[data-channel-tab="settings"]');
  await browser("click",'[data-page-edit]');
  await browser("fill",'[name="_name"]',secondName);
  await browser("eval",'document.querySelector("[data-page-save]").scrollIntoView({block:"center"})');
  await browser("click",'[data-page-save]');
  await wait(`document.querySelector('.channel-form-error').textContent.includes('同名')`);
  await browser("fill",'[name="_name"]',firstName);
  await browser("click",'[data-channel-tab="history"]');
  await browser("eval",'document.querySelector("[data-reset-conversation]").scrollIntoView({block:"center"})');
  await browser("click",'[data-reset-conversation]');
  await wait(`document.querySelector('.channel-dialog')`);
  await browser("click",'.channel-dialog [type="submit"]');
  await wait(`!document.querySelector('.channel-dialog')`);
  const reset = await (await fetch(base+'/api/channel-gateway')).json();
  assert.notEqual(reset.connections.find(c=>c.id===created[0].id).conversation_id,created[0].conversation_id);
  await browser("click",'.header-back');
  await wait(`document.querySelector('.channel-connections')`);
  for(const account of created) {
    await browser("click",'[data-remove-account="'+account.id+'"]');
    await wait(`document.querySelector('.channel-dialog-body').textContent.includes('无法恢复')`);
    await browser("click",'.channel-dialog [type="submit"]');
    await wait(`!document.querySelector('[data-config="${account.id}"]') && !document.querySelector('.channel-dialog')`);
  }
  await browser("click", '[data-act="import"]');
  await browser("click", '.channel-dialog [type="submit"]');
  await wait(
    `document.querySelector('.channel-form-error').textContent==='请选择 ZIP 文件'`,
  );
  await browser("upload",'.channel-upload input',new URL("../static/connector-example.zip",import.meta.url).pathname);
  await wait(`document.querySelector('.channel-upload strong').textContent==='connector-example.zip'`);
  await browser("click",'[data-open-guide]');
  await wait(`document.querySelector('.channel-guide-open') && document.querySelector('.connector-guide-content').textContent.includes('先跑通示例')`);
  for(const section of ["package","protocol","test"]) {
    await browser("click",'[data-section="'+section+'"]');
    await wait(`document.querySelector('[data-section="${section}"]').getAttribute('aria-current')==='page'`);
  }
  await browser("click",'[data-section="package"]');
  await wait(`document.querySelector('.connector-guide-content .cm-editor')`);
  const download=await fetch(base+"/connector-example.zip");
  assert(download.ok);
  const bytes=new Uint8Array(await download.arrayBuffer());
  assert.equal(bytes[0],0x50); assert.equal(bytes[1],0x4b);
  await browser("click",'[data-guide-back]');
  await wait(`!document.querySelector('.channel-guide-open') && document.querySelector('.channel-upload strong').textContent==='connector-example.zip'`);
  await browser("press", "Escape");
  await wait(`!document.querySelector('.channel-dialog')`);
  await browser("click", ".header-back");
  await wait(`location.hash==='#overview'`);
  assert.equal(
    (await (await fetch(base + "/api/config")).json()).revision,
    before,
  );
  console.log(
    "PASS channel install, isolated accounts, management page, duplicate validation, conversation reset, back navigation, connector guide; core configuration unchanged",
  );
} finally {
  await browser("close").catch(() => {});
}
