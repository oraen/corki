import { python, fixtureCommand } from './platform.mjs';
import { _electron as electron } from "playwright";
import http from "node:http";
import { mkdtemp, mkdir, writeFile, readFile, access } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
const home = await mkdtemp(
  path.join(os.tmpdir(), "corki-desktop-interactions-"),
);

const mcp = path.join(home, "fixture_mcp.py");
await writeFile(
  mcp,
  `
import json, sys, os
from pathlib import Path
with open(Path(__file__).with_name('mcp-pids.txt'),'a') as pids:
    pids.write(str(os.getpid())+'\\n')
pending=None
def send(value):
    print(json.dumps(dict(jsonrpc='2.0',**value)),flush=True)
for line in sys.stdin:
    req=json.loads(line)
    method=req.get('method')
    if method=='initialize':
        send(dict(id=req['id'],result=dict(protocolVersion='2025-06-18',capabilities={'tools':{}},serverInfo={'name':'fixture','version':'1'})))
    elif method=='tools/list':
        send(dict(id=req['id'],result={'tools':[{'name':'probe','description':'Request fixture form','inputSchema':{'type':'object','properties':{'mode':{'type':'string','enum':['form','url']}}},'annotations':{'readOnlyHint':False}}]}))
    elif method=='tools/call':
        pending=req['id']
        if req.get('params',{}).get('arguments',{}).get('mode')=='url':
            send(dict(id='fixture-url',method='elicitation/create',params={'mode':'url','message':'网页确认测试','url':'https://example.invalid/fixture','elicitationId':'fixture-url-flow'}))
            continue
        send(dict(id='fixture-form',method='elicitation/create',params={
            'message':'填写测试参数','mode':'form','requestedSchema':{
                'type':'object','properties':{
                    'label':{'type':'string','title':'名称','minLength':2},
                    'count':{'type':'integer','title':'次数','minimum':1,'maximum':3},
                    'enabled':{'type':'boolean','title':'启用','default':True},
                    'tags':{'type':'array','title':'选项','minItems':1,'items':{'type':'string','enum':['alpha','beta']}}
                },'required':['label','count','tags']
            }}))
    elif req.get('id') in ('fixture-form','fixture-url'):
        send(dict(id=pending,result={'content':[{'type':'text','text':json.dumps(req.get('result'),ensure_ascii=False)}]}))
    elif method=='ping':
        send(dict(id=req['id'],result={}))
`,
);
const requests = [];
const server = http.createServer(async (req, res) => {
  try {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    const body = JSON.parse(raw);
    requests.push(body);
    res.writeHead(200, { "Content-Type": "text/event-stream" });
    const lastUser = body.messages.findLastIndex(message => message.role === "user");
    const toolResult = body.messages.slice(lastUser + 1).findLast(message => message.role === "tool");
    let delta, finish;
    if (toolResult) {
      delta = { content: "执行结果：" + String(toolResult.content) };
      finish = "stop";
    } else {
      const text = JSON.stringify(body.messages);
      const names = body.tools.map((tool) => tool.function?.name || tool.name);
      const patchMode = text.includes("patch accept") || text.includes("patch decline");
      const mcpMode = text.includes("MCP form") || text.includes("MCP url");
      const name = names.find((name) =>
        mcpMode
          ? name.startsWith("corki_ns_") || name.endsWith("probe")
          : name.endsWith(patchMode ? "apply_patch" : "exec_command"),
      );
      assert(name, `Missing fixture tool: ${names.join(", ")}`);
      delta = {
        tool_calls: [
          {
            index: 0,
            id: randomUUID(),
            type: "function",
            function: {
              name,
              arguments: JSON.stringify(
                mcpMode
                  ? (text.includes("MCP url") ? {mode:"url"} : {})
                  : patchMode ? {patch: "*** Begin Patch\n*** Add File: " + (text.includes("patch accept") ? "patch-accepted.txt" : "patch-declined.txt") + "\n+DESKTOP_PATCH_FIXTURE\n*** End Patch"} : fixtureCommand(python, ['-c', "print('DESKTOP_TOOL_FIXTURE', end='')"]),
              ),
            },
          },
        ],
      };
      finish = "tool_calls";
    }
    res.write(
      `data: ${JSON.stringify({ choices: [{ index: 0, delta, finish_reason: null }] })}\n\n`,
    );
    res.end(
      `data: ${JSON.stringify({ choices: [{ index: 0, delta: {}, finish_reason: finish }] })}\n\ndata: [DONE]\n\n`,
    );
  } catch (error) {
    console.error(error);
    res.destroy(error);
  }
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
await mkdir(path.join(home, "rules"));
await writeFile(
  path.join(home, "rules/default.rules"),
  'prefix_rule(pattern=["printf"], decision="prompt", justification="Desktop fixture approval")\n',
);
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
    tools: { mode: "direct", search_mode: "disabled" },
    approval_policy: "on-request",
    skills: { enabled: false },
    memories: { generate: false, use: false },
    features: { goals: false, plugins: false },
    mcp: {
      approval_policy: "on-request",
      servers: {
        fixture: {
          transport: "stdio",
          command: python,
          args: [mcp],
          required: true,
          default_tools_approval_mode: "prompt",
        },
      },
    },
  }),
);
let app;
try {
  const env = { ...process.env, CORKI_HOME: home, CORKI_PYTHON: python };
  delete env.ELECTRON_RUN_AS_NODE;
  app = await electron.launch({ args: [path.resolve(".")], env });
  await app.evaluate(({ dialog }) => {
    dialog.showMessageBox = async () => ({
      response: 1,
      checkboxChecked: false,
    });
  });
  const page = await app.firstWindow();
  const failures = [];
  page.on("pageerror", (error) => failures.push(error.message));
  const input = page.getByRole("textbox", { name: "消息", exact: true });
  async function submit(text) {
    await input.fill(text);
    await page.getByRole("button", { name: "发送消息" }).click();
  }
  await submit("shell allow");
  const review = page.locator(".interaction");
  await review
    .locator(".approval-details pre")
    .filter({ hasText: "DESKTOP_TOOL_FIXTURE" })
    .waitFor({ timeout: 20000 });
  assert(
    (await review.locator(".approval-details").innerText()).includes("argv"),
  );
  await page.screenshot({ path: path.join(home, "execution-approval.png") });
  const scope = review.getByRole("button", { name: "批准范围", exact: true });
  await scope.focus();
  await scope.press("ArrowDown");
  await page.getByRole("option", {name:"仅本次",exact:true}).press("End");
  await page.getByRole("option", {name:"本会话",exact:true}).press("Enter");
  await review.getByRole("button", { name: "本会话允许", exact: true }).click();
  await page
    .locator(".markdown")
    .filter({ hasText: "DESKTOP_TOOL_FIXTURE" })
    .waitFor({ timeout: 20000 });
  await submit("shell repeat");
  await page.locator(".message.assistant").nth(1).filter({hasText:"DESKTOP_TOOL_FIXTURE"}).waitFor({timeout:20000});
  assert.equal(await review.count(),0,"Session approval must be honored by the actual runtime");
  await page.locator(".new-chat").click();
  await submit("shell deny");
  await review.getByRole("button", { name: "拒绝", exact: true }).click();
  await page
    .locator(".markdown")
    .filter({ hasText: "rejected" })
    .waitFor({ timeout: 20000 });
  await page.locator(".new-chat").click();
  await submit("MCP form");
  await review
    .getByText("Allow the fixture MCP server", { exact: false })
    .waitFor({ timeout: 20000 });
  await review.getByRole("button", { name: "确认", exact: true }).click();
  await review.getByText("填写测试参数", { exact: true }).waitFor();
  await review.getByRole("button", { name: "确认", exact: true }).click();
  await page.locator(".inline-error").filter({ hasText: "请填写" }).waitFor();
  await review
    .getByRole("textbox", { name: "名称", exact: true })
    .fill("desktop");
  await review.getByRole("spinbutton", { name: "次数", exact: true }).fill("9");
  await review.getByRole("button", { name: "alpha", exact: true }).click();
  await review.getByRole("button", { name: "确认", exact: true }).click();
  await page
    .locator(".inline-error")
    .filter({ hasText: "次数 的值不符合要求" })
    .waitFor();
  await review.getByRole("spinbutton", { name: "次数", exact: true }).fill("2");
  await page.screenshot({ path: path.join(home, "mcp-form.png") });
  await review.getByRole("button", { name: "确认", exact: true }).click();
  await page
    .locator(".markdown")
    .filter({ hasText: "desktop" })
    .waitFor({ timeout: 20000 });
  const result = requests
    .findLast((r) => r.messages.some((m) => m.role === "tool"))
    .messages.findLast((m) => m.role === "tool").content;
  assert(String(result).includes("desktop"));
  assert(String(result).includes("alpha"));
  assert(String(result).includes("true"));
  assert.equal(await review.count(), 0);
  for (const [button, action] of [["已完成", "accept"], ["拒绝", "decline"], ["取消请求", "cancel"]]) {
    await page.locator(".new-chat").click();
    await submit("MCP url " + action);
    await review.getByText("Allow the fixture MCP server", {exact:false}).waitFor({timeout:20000});
    await review.getByRole("button", {name:"确认",exact:true}).click();
    await review.getByText("网页确认测试", {exact:true}).waitFor();
    await review.getByRole("button", {name:button,exact:true}).click();
    await page.locator(".markdown").filter({hasText:action}).waitFor({timeout:20000});
    const response=requests.findLast(r=>r.messages.some(m=>m.role==='tool')).messages.findLast(m=>m.role==='tool').content;
    assert(String(response).includes(action));
    assert.equal(await review.count(),0);
  }
  for (const action of ["accept", "decline"]) {
    await page.locator(".new-chat").click();
    await submit("patch " + action);
    await review.getByText("文件变更", {exact:true}).waitFor({timeout:20000});
    const target=path.join(home,"main/default",action==='accept'?'patch-accepted.txt':'patch-declined.txt');
    await assert.rejects(access(target));
    await review.getByRole("button", {name:action==='accept'?"允许本次":"拒绝",exact:true}).click();
    if(action==='accept') {
      await review.getByText("上次执行结果（已有修改未回滚）", {exact:true}).waitFor({timeout:20000});
      await assert.rejects(access(target));
      await review.getByRole("button", {name:"允许本次",exact:true}).click();
    }
    await page.locator(".markdown").waitFor({timeout:20000});
    if(action==='accept') assert.equal(await readFile(target,'utf8'),'DESKTOP_PATCH_FIXTURE\n');
    else await assert.rejects(access(target));
    assert.equal(await review.count(),0);
  }
  assert.deepEqual(failures, []);
  console.log(
    "PASS native Agent shell allow/deny, MCP tool approval, elicitation form validation, typed defaults, response and actual MCP URL accept/decline/cancel and native patch consent before writes",
  );
} catch (error) {
  if (app) {
    const page = await app.firstWindow();
    console.error((await page.locator("body").innerText()).slice(-6000));
  }
  throw error;
} finally {
  if (app) await app.close();
  const pids = await readFile(path.join(home, "mcp-pids.txt"), "utf8").catch(
    () => "",
  );
  for (const pid of pids.trim().split("\n").filter(Boolean)) {
    assert.throws(
      () => process.kill(Number(pid), 0),
      { code: "ESRCH" },
      "Owned MCP process must exit with its desktop host",
    );
  }
  server.closeAllConnections();
  server.close();
  console.log("Disposable test home:", home);
}
