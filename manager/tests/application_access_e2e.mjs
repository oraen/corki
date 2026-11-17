// Disposable application_fixture.py only; Kafka test connections stay disabled.
import {execFileSync} from 'node:child_process';
import assert from 'node:assert/strict';

const base=process.env.CORKI_APPLICATION_TEST_URL;
if(!base?.startsWith('http://127.0.0.1:'))throw Error('Disposable fixture URL required');
const cli=(...args)=>execFileSync('agent-browser',['--session','corki-application-access',...args],{encoding:'utf8'}).trim();
const ev=code=>JSON.parse(cli('eval',code));
const click=selector=>cli('click',selector);
const fill=(selector,text)=>cli('fill',selector,text);
const wait=async code=>{for(let i=0;i<80;i++){if(ev(`Boolean(${code})`))return;await new Promise(r=>setTimeout(r,100));}throw Error('Timeout '+code);};
const tab=async()=>{await wait('document.querySelector("[data-app-tab=access]")');click('[data-app-tab=access]');await wait('document.querySelector("[name=http_enabled]")');};
const api=(action,values={},write=false)=>ev(`(async()=>{const response=await fetch('/api/applications'${write?'':"+'?'+new URLSearchParams("+JSON.stringify({action,...values})+")"},${write?JSON.stringify({method:'POST',headers:{'Content-Type':'application/json','X-Corki-Manager':'1'},body:JSON.stringify({action,...values})}):'{}'});return {status:response.status,...await response.json()};})()`);

cli('open',base+'/#applications');
await wait('document.querySelector(".application-card")');
const app=api('list').items[0];
await wait('document.querySelector("[data-application-kafka=toggle]")&&!document.querySelector("[data-application-kafka=toggle]").disabled');
assert(ev('(()=>{const a=document.querySelector("[data-application-gateway-host]").getBoundingClientRect();const k=document.querySelector("[data-application-kafka-host]").getBoundingClientRect();return Math.abs(k.top-a.top)<1&&Math.abs(k.left-a.right)<1&&Math.abs(a.width-k.width)<1;})()'));
click('[data-application-kafka=toggle]');
await wait('!window.CorkiApplications.kafkaUI.changing');
assert.equal(api('kafka').global_state.enabled,true);
click('[data-application-kafka=settings]');
await wait('document.querySelector("[data-access-action=global-toggle]")');
assert.equal(ev('document.querySelector("[data-access-action=global-toggle]").getAttribute("aria-checked")'),'true');
click('[data-access-action=global-toggle]');
await wait('!window.CorkiApplications.accessUI.savingGlobal');
cli('open',base+'/#applications');
await wait('document.querySelector("[data-application-kafka=toggle]")&&!document.querySelector("[data-application-kafka=toggle]").disabled');
assert.equal(ev('document.querySelector("[data-application-kafka=toggle]").getAttribute("aria-checked")'),'false');
cli('set','viewport','1440','900');
cli('screenshot','/tmp/corki-kafka-overview-halves.png');
cli('open',base+'/#applications/'+app.id);
await tab();
assert(ev('document.querySelector("[name=http_enabled]").checked'));
assert(!ev('document.querySelector("[name=global_kafka]").checked'));
assert(ev('document.querySelector("[data-app-action=edit]").hidden'));
click('[name=http_enabled]');
await wait('!window.CorkiApplications.accessUI.savingFlags');
assert.equal(api('access',{app:app.id}).http_enabled,false);
cli('reload');await tab();
assert(!ev('document.querySelector("[name=http_enabled]").checked'));
click('[name=http_enabled]');await wait('!window.CorkiApplications.accessUI.savingFlags');
click('[name=global_kafka]');await wait('!window.CorkiApplications.accessUI.savingFlags');
assert.equal(api('detail',{app:app.id}).version,app.version);

const add=async(name,{security=false}={})=>{
 click('[data-access-action=add]');
 click('[data-save]');
 await wait('!document.querySelector(".app-kafka-dialog [data-error]").hidden');
 fill('.app-kafka-dialog [name=name]',name);
 fill('.app-kafka-dialog [name=brokers]','localhost:19092, localhost:29092');
 fill('[name=topic-entry]','questions, followup');
 cli('press','Enter');
 assert.equal(ev('document.querySelectorAll("[data-topics] > span").length'),2);
 fill('[name=output_topic]','answers');
 if(security){
  assert(ev('document.querySelector(".app-kafka-security h3").textContent==="认证与安全设置"'));
  assert(!ev('Boolean(document.querySelector("details.app-kafka-security"))'));
  click('[data-kafka-select=security_protocol] summary');
  await wait('document.querySelector("[data-kafka-select=security_protocol] .application-menu").style.position==="fixed"');
  assert(ev('(()=>{const d=document.querySelector("[data-kafka-select=security_protocol]");const m=d.querySelector(".application-menu").getBoundingClientRect();const s=d.querySelector("summary").getBoundingClientRect();return Math.abs(m.top-s.bottom)<2&&m.bottom<innerHeight;})()'));
  cli('press','Escape');
  assert(ev('document.querySelector(".app-kafka-dialog").open'));
  click('[data-kafka-select=security_protocol] summary');
  click('[data-select-value=SASL_PLAINTEXT]');
  fill('[name=sasl_username]','test-user');
  fill('[name=sasl_password]','test-only-password');
 }
 click('.app-kafka-dialog [name=enabled]');
 click('[data-save]');
 await wait('!document.querySelector(".app-kafka-dialog")');
};
await add('测试集群一',{security:true});
await add('测试集群二');
assert.equal(ev('document.querySelectorAll("[data-kafka-card]").length'),2);
const data=api('kafka',{app:app.id});
assert.equal(data.connections.length,2);
assert(!JSON.stringify(data).includes('test-only-password'));
assert.equal(data.connections[0].has_sasl_password,true);
click('[data-access-action=edit]');
assert.equal(ev('document.querySelector("[name=sasl_password]").value'),'');
assert.equal(ev('document.querySelector("[name=sasl_password]").placeholder'),'已配置');
click('.app-kafka-dialog [aria-label=关闭]');
for(const [width,height]of [[1280,800],[1440,900],[1920,1080]]){
 cli('set','viewport',String(width),String(height));
 assert(ev('document.documentElement.scrollWidth<=innerWidth'));
 cli('screenshot',`/tmp/corki-application-access-${width}.png`);
}
// Access state changes must not destroy a prompt draft/editor.
click('[data-app-tab=prompt]');click('[data-app-action=edit]');
cli('focus','.application-prompt .cm-content');cli('press','Meta+a');cli('keyboard','inserttext','切换接入配置仍保留的草稿');
ev('window.__accessEditor=window.CorkiApplications.editor;true');
await tab();click('[name=global_kafka]');await wait('!window.CorkiApplications.accessUI.savingFlags');
click('[data-app-tab=prompt]');
assert(ev('window.__accessEditor===window.CorkiApplications.editor'));
assert.equal(ev('window.CorkiApplications.form.prompt'),'切换接入配置仍保留的草稿');
click('[data-app-action=cancel-edit]');
await tab();
click('[data-access-action=delete]');
assert(ev('document.querySelector(".app-kafka-confirm").textContent.includes("测试集群一")'));
click('.app-kafka-confirm [data-delete]');
await wait('document.querySelectorAll("[data-kafka-card]").length===1');
click('.app-access-global-link');
await wait('document.querySelector("[data-access-action=global-toggle]")');
assert(ev('document.querySelector(".breadcrumbs").textContent.includes("Kafka 接入")'));
await add('全局集群');
click('[data-access-action=global-toggle]');
await wait('!window.CorkiApplications.accessUI.savingGlobal');
assert.equal(api('kafka').global_state.enabled,true);
click('[data-access-action=global-toggle]');
await wait('!window.CorkiApplications.accessUI.savingGlobal');
assert.equal(api('kafka').global_state.enabled,false);
cli('screenshot','/tmp/corki-application-kafka-global.png');
// No live connections are left behind in the fixture.
assert(api('kafka').connections.every(c=>!c.enabled));
assert.equal(api('kafka',{app:app.id}).connections.length,1);
assert.equal(api('detail',{app:app.id}).version,app.version);
console.log('Application access: flags, persistence, revisions, multi-cluster CRUD, credentials, menus, layout, draft isolation and global configuration passed');
