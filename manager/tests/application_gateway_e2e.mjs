// Use application_fixture.py only; never a user's Manager/configuration.
import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";
import net from "node:net";
const base=process.env.CORKI_APPLICATION_TEST_URL;
if(!base?.startsWith("http://127.0.0.1:"))throw Error("Set disposable application fixture URL");
const cli=(...args)=>execFileSync("agent-browser",["--session","corki-application-gateway",...args],{encoding:"utf8"}).trim();
const evaluate=script=>JSON.parse(cli("eval",script));
async function wait(script){for(let n=0;n<100;n++){if(evaluate(`Boolean(${script})`))return;await new Promise(r=>setTimeout(r,100));}throw Error(script);}
const click=selector=>cli("click",selector);
cli("open",base+"/#applications");cli("set","viewport","1440","900");cli("reload");
await wait('document.querySelector("[data-application-gateway=toggle]")&&!document.querySelector("[data-application-gateway=toggle]").disabled');
if(evaluate('document.querySelector("[data-application-gateway=toggle]").getAttribute("aria-checked")==="true"')){
 click('[data-application-gateway=toggle]');await wait('document.querySelector(".application-gateway-state").textContent==="已关闭"');
}
click('[data-application-gateway=settings]');
await wait('document.querySelector(".application-gateway-dialog")?.open');
assert(evaluate('document.querySelector("[aria-label=网关访问密钥]").type==="password"'));
click('[name=gateway-port-mode][value=manual]');
cli("fill","[name=gateway-port]","-1");click('[data-gateway-save]');
assert(evaluate('document.querySelector(".application-gateway-dialog").open&&document.querySelector("[name=gateway-port]").validity.rangeUnderflow'));
const server=net.createServer();await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));const port=server.address().port;await new Promise(resolve=>server.close(resolve));
cli("fill","[name=gateway-port]",String(port));click('[data-gateway-save]');
await wait('!document.querySelector(".application-gateway-dialog")&&!document.querySelector("[data-application-gateway=toggle]").disabled');
click('[data-application-gateway=toggle]');
assert(evaluate('Boolean(document.querySelector(".application-list-toolbar input:not(:disabled)"))'));
await wait(`document.querySelector(".application-gateway-port-number")?.textContent==="${port}"&&document.querySelector(".application-gateway-state").textContent==="运行中"&&!document.querySelector("[data-application-gateway=toggle]").disabled`);
click('[data-application-gateway=settings]');await wait('document.querySelector(".application-gateway-dialog")?.open');
const key=evaluate('document.querySelector("[aria-label=网关访问密钥]").value');
assert(/^[a-f0-9]{32}$/.test(key));
click('[data-gateway-reveal]');assert(evaluate('document.querySelector("[aria-label=网关访问密钥]").type==="text"'));click('[data-gateway-reveal]');
evaluate('Object.defineProperty(navigator,"clipboard",{configurable:true,value:{writeText:async text=>{window.__gatewayCopy=text;}}});true');
click('[data-gateway-copy-key]');
assert.equal(evaluate("window.__gatewayCopy"),key);
assert(evaluate('document.querySelector("pre.application-gateway-example").textContent.includes("Bearer "+document.querySelector("[aria-label=网关访问密钥]").value)'));
assert(evaluate('!document.querySelector("[name=example-app]")&&!document.querySelector("[data-gateway-copy-example]")'));
assert(evaluate('document.querySelector("pre.application-gateway-example").textContent.includes("appid=#{appid}")'));
assert(evaluate('!document.querySelector(".application-gateway-dialog").textContent.includes("自动模式优先使用")'));
const appid=evaluate('document.querySelector(".application-card-id > span:nth-child(2)").textContent');
cli("screenshot","/tmp/corki-app-gateway-1440.png");
cli("set","viewport","1280","800");assert(evaluate('document.documentElement.scrollWidth<=innerWidth'));
cli("screenshot","/tmp/corki-app-gateway-1280.png");
click('.application-gateway-dialog [data-gateway-close]');
const result=await fetch(`http://127.0.0.1:${port}/app?appid=${appid}`,{method:"POST",headers:{Authorization:"Bearer "+key,"Content-Type":"application/json"},body:JSON.stringify({input:"浏览器端到端网关验证",request_id:"browser-gateway-"+Date.now()})});
const view=await result.json();assert.equal(result.status,200);assert.equal(view.status,"completed");assert(view.output.includes("应用已接通"));
// Actual API work did not navigate away from the application list.
assert.equal(evaluate('location.hash'),"#applications");
cli("reload");await wait('document.querySelector(".application-gateway-port-number")');
assert(evaluate(`document.querySelector(".application-gateway-port-number").textContent==="${port}"`));
assert(evaluate('!document.querySelector(".application-gateway-address")&&!document.querySelector(".application-gateway").textContent.includes("开启后，可通过")'));
cli("set","viewport","1440","900");
cli("hover",".application-gateway-help");
await wait('getComputedStyle(document.querySelector(".application-gateway-help"),"::after").opacity==="1"');
assert(evaluate(`getComputedStyle(document.querySelector(".application-gateway-help"),"::after").content.includes(":${port}/app?appid=")`));
cli("screenshot","/tmp/corki-app-gateway-compact-tooltip.png");
// Idle header does not poll continuously.
evaluate('window.__gatewayFetch=window.fetch;window.__gatewayStatusCalls=0;window.fetch=(...args)=>{if(String(args[0]).includes("action=gateway-status"))window.__gatewayStatusCalls++;return window.__gatewayFetch(...args)};true');
await new Promise(r=>setTimeout(r,1100));assert.equal(evaluate('window.__gatewayStatusCalls'),0);
evaluate('window.fetch=window.__gatewayFetch;true');
click('[data-application-gateway=toggle]');await wait('document.querySelector(".application-gateway-state").textContent==="已关闭"');
assert.equal(cli("errors"),"");
console.log("Gateway UI passed: port validation/save, nonblocking start/stop, actual port, masked key/copy, JSON call, refresh recovery, no idle polling, 1280/1440 layout");
