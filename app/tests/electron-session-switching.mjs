// Codex ordinary thread settings semantics: A -> A (steer) -> B (next turn).
// All model calls, configuration and history stay inside this disposable home.
import {_electron as electron} from 'playwright';
import assert from 'node:assert/strict';
import http from 'node:http';
import {mkdtemp, writeFile, readFile} from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {python} from './platform.mjs';
import {poll} from './wait.mjs';

const home = await mkdtemp(path.join(os.tmpdir(), 'corki-session-switching-'));
const requests = [], releases = [];
const server = http.createServer(async (req, res) => {
  let raw = '';
  for await (const piece of req) raw += piece;
  const body = JSON.parse(raw);
  requests.push(body);
  res.writeHead(200, {'Content-Type': 'text/event-stream'});
  res.write(`data: ${JSON.stringify({choices: [{index: 0, delta: {content: `Reply from ${body.model}.`}, finish_reason: null}]})}\n\n`);
  await new Promise(resolve => releases.push(resolve));
  if (!res.destroyed)
    res.end('data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n');
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const model = {context_window: 262144, supported_reasoning_levels: [{effort: 'low'}, {effort: 'high'}]};
const config = {
  default: {provider: 'alpha', model: 'small'},
  providers: Object.fromEntries([['alpha', 'small'], ['beta', 'large']].map(([id, name]) => [id, {
    base_url: `http://127.0.0.1:${server.address().port}/v1`, api_mode: 'chat_completions',
    api_key: 'fixture-not-a-credential', models: {[name]: {...model, reasoning_effort: 'low'}},
  }])), features: {goals: false}, skills: {enabled: false}, memories: {enabled: false},
};
await writeFile(path.join(home, 'settings.json'), JSON.stringify(config));
const original = await readFile(path.join(home, 'settings.json'), 'utf8');
const env = {...process.env, CORKI_HOME: home, CORKI_PYTHON: python};
delete env.ELECTRON_RUN_AS_NODE;
const packaged = process.env.CORKI_TEST_PACKAGED;
let app;
try {
  app = await electron.launch({
    ...(packaged ? {executablePath: path.resolve(packaged)} : {}),
    args: [...(packaged ? [] : [path.resolve('.')]),
      ...(process.env.CORKI_SETTINGS_CDP ? ['--remote-debugging-port=' + process.env.CORKI_SETTINGS_CDP] : [])],
    env,
  });
  await app.evaluate(({dialog}) => {
    dialog.showMessageBox = async () => ({response: 1, checkboxChecked: false});
  });
  const page = await app.firstWindow(), errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const input = page.getByRole('textbox', {name: '消息', exact: true});
  const send = page.getByRole('button', {name: /^(发送消息|追加消息)$/});
  const open = async () => {
    await page.getByRole('button', {name: '会话设置', exact: true}).click();
    const dialog = page.getByRole('dialog');
    await dialog.waitFor();
    return dialog;
  };
  const choose = async (dialog, label, value) => {
    await dialog.getByRole('button', {name: label, exact: true}).click();
    await dialog.getByRole('listbox', {name: label, exact: true})
      .locator(`[role=option][data-value=${JSON.stringify(value)}]`).click();
  };
  const save = async dialog => {
    await dialog.getByRole('button', {name: '保存', exact: true}).click();
    await dialog.waitFor({state: 'hidden'});
  };
  await input.waitFor();
  await input.fill('First task');
  await send.click();
  await poll(() => requests.length === 1);
  await page.getByRole('button', {name: '停止执行', exact: true}).waitFor();
  const thread = (await page.evaluate(() => window.corki.request('status'))).running[0];
  let dialog = await open();
  await dialog.getByText('修改将在下一轮对话生效，当前任务和补充消息继续使用原设置', {exact: true}).waitFor();
  await choose(dialog, '供应商', 'beta');
  await choose(dialog, '模型', 'large');
  await choose(dialog, '推理等级', 'high');
  await page.screenshot({path: path.join(home, 'running-settings.png')});
  await save(dialog);
  assert.equal(requests.length, 1, 'Saving must not cancel or rerun the current model request');
  assert.equal((await page.evaluate(thread => window.corki.request('threads.read', {threadId: thread}), thread)).model.model, 'small');
  await page.locator('.new-chat').click();
  dialog = await open();
  assert.equal(await dialog.getByLabel('供应商', {exact: true}).getAttribute('data-value'), '');
  await dialog.getByRole('button', {name: '取消', exact: true}).click();
  await page.getByRole('button', {name: 'First task', exact: true}).click();
  dialog = await open();
  assert.equal(await dialog.getByLabel('模型', {exact: true}).getAttribute('data-value'), 'large');
  await dialog.getByRole('button', {name: '取消', exact: true}).click();
  await input.fill('Supplement the current task');
  await send.click();
  await page.locator('.user-text').filter({hasText: 'Supplement the current task'}).waitFor();
  await poll(async () => !(await page.evaluate(thread => window.corki.request('state.read', {key: 'draft:' + thread}), thread)).request);
  let stored = await page.evaluate(thread => window.corki.request('state.read', {key: 'draft:' + thread}), thread);
  assert.equal(stored.model, 'large', 'The active-turn snapshot must not erase pending model choices');
  assert.equal(stored.sessionSettings.reasoning, 'high');
  assert.equal(await page.locator('.followups,.followup-row').count(), 0);
  releases[0]();
  await poll(() => requests.length === 2);
  assert.deepEqual(requests.map(r => r.model), ['small', 'small']);
  assert(JSON.stringify(requests[1].messages).includes('Supplement the current task'));
  // Reloading the view must retain the running task and the next-turn choice.
  await page.reload();
  await page.getByRole('button', {name: '停止执行', exact: true}).waitFor();
  dialog = await open();
  assert.equal(await dialog.getByLabel('模型', {exact: true}).getAttribute('data-value'), 'large');
  await dialog.getByRole('button', {name: '取消', exact: true}).click();
  releases[1]();
  await poll(async () => !(await page.evaluate(() => window.corki.request('status'))).running.length);
  await input.fill('Next turn with the selected model');
  await send.click();
  await poll(() => requests.length === 3);
  assert.deepEqual(requests.map(r => r.model), ['small', 'small', 'large']);
  assert.equal(requests[2].reasoning_effort, 'high');
  // Restore globals while B runs; stopping must not discard those defaults.
  dialog = await open();
  await dialog.getByRole('button', {name: '恢复全局设置', exact: true}).click();
  await save(dialog);
  await page.getByRole('button', {name: '停止执行', exact: true}).click();
  await poll(async () => !(await page.evaluate(() => window.corki.request('status'))).running.length);
  releases[2]();
  await input.fill('After stopping, use global model');
  await send.click();
  await poll(() => requests.length === 4);
  assert.equal(requests[3].model, 'small');
  releases[3]();
  await poll(async () => !(await page.evaluate(() => window.corki.request('status'))).running.length);
  stored = await page.evaluate(thread => window.corki.request('state.read', {key: 'draft:' + thread}), thread);
  assert.deepEqual(stored.sessionSettings, {});
  await page.locator('.new-chat').click();
  dialog = await open();
  assert.equal(await dialog.getByLabel('供应商', {exact: true}).getAttribute('data-value'), '');
  await dialog.getByRole('button', {name: '取消', exact: true}).click();
  await page.getByRole('button', {name: 'First task', exact: true}).click();
  dialog = await open();
  assert.equal(await dialog.getByLabel('模型', {exact: true}).getAttribute('data-value'), '');
  await dialog.getByRole('button', {name: '取消', exact: true}).click();
  assert.equal(await readFile(path.join(home, 'settings.json'), 'utf8'), original);
  assert.deepEqual(errors, []);
  console.log('PASS running settings, A/A/B model routing, steer/reload, stop/reset, conversation isolation and unchanged globals');
  console.log('Screenshot:', path.join(home, 'running-settings.png'));
} catch (error) {
  if (app) {
    const page = await app.firstWindow();
    console.error('TEST_HOME', home, (await page.locator('body').innerText()).slice(-3000));
    await page.screenshot({path: path.join(home, 'failure.png')});
  }
  throw error;
} finally {
  releases.forEach(release => release());
  if (app) await app.close();
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
}
