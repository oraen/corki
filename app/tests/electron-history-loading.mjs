// Hold history IPC responses to inspect every intermediate conversation state.
import {_electron as electron} from 'playwright';
import assert from 'node:assert/strict';
import {mkdtemp, writeFile, rm} from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {python} from './platform.mjs';
import {poll} from './wait.mjs';

const home = await mkdtemp(path.join(os.tmpdir(), 'corki-history-loading-'));
await writeFile(path.join(home, 'settings.json'), '{}');
const env = {...process.env, CORKI_HOME: home, CORKI_PYTHON: python};
delete env.ELECTRON_RUN_AS_NODE;
let app;
try {
  const packaged = process.env.CORKI_TEST_PACKAGED;
  app = await electron.launch({
    ...(packaged ? {executablePath: path.resolve(packaged)} : {}),
    args: packaged ? [] : [path.resolve('.')], env,
  });
  const page = await app.firstWindow();
  await page.getByRole('button', {name: '打开管理页面', exact: true}).waitFor();
  await app.evaluate(({ipcMain}) => {
    globalThis.fixtureReads = [];
    globalThis.fixtureRequests = [];
    const state = new Map();
    ipcMain.removeHandler('corki:request');
    ipcMain.handle('corki:request', async (_, method, params) => {
      globalThis.fixtureRequests.push(method);
      if (method === 'initialize') return {threadManagementVersion: 2, instance: 'fixture', sequence: 0};
      if (method === 'state.write') {state.set(params.key, params.value); return null;}
      if (method === 'state.read') return state.get(params.key) || null;
      if (method === 'config.read') return {valid: true, providers: []};
      if (method === 'threads.list') return {
        threads: ['Alpha', 'Beta', 'Failure'].map(id => ({id, title: id, running: false})),
        directories: [], next: null,
      };
      if (method === 'threads.read') return new Promise((resolve, reject) => {
        globalThis.fixtureReads.push({id: params.threadId, resolve, reject});
      });
      throw new Error('Unexpected fixture method: ' + method);
    });
  });
  await page.reload();
  await page.locator('.welcome').waitFor();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const open = name => page.getByRole('button', {name, exact: true}).click();
  const pending = id => poll(() => app.evaluate((_, id) =>
    globalThis.fixtureReads.some(read => read.id === id), id));
  const finish = id => app.evaluate((_, id) => {
    const index = globalThis.fixtureReads.findIndex(read => read.id === id);
    const [read] = globalThis.fixtureReads.splice(index, 1);
    read.resolve({threadId: id, instance: 'fixture', sequence: 0, directory: '', running: false,
      messages: [{id: id + '-message', role: 'assistant', content: id + ' answer'}],
      live: [], pending: [], next: null});
  }, id);
  const monitor = () => page.evaluate(() => {
    globalThis.welcomeFlashes = 0;
    globalThis.welcomeObserver?.disconnect();
    globalThis.welcomeObserver = new MutationObserver(() => {
      if (document.querySelector('.welcome')) globalThis.welcomeFlashes++;
    });
    globalThis.welcomeObserver.observe(document.querySelector('.conversation-inner'), {childList: true, subtree: true});
  });
  await open('Alpha');
  await pending('Alpha');
  await page.getByRole('status', {name: '加载会话', exact: true}).waitFor();
  assert.equal(await page.locator('.welcome').count(), 0);
  await finish('Alpha');
  await page.getByText('Alpha answer', {exact: true}).waitFor();
  await monitor();
  await open('Beta');
  await pending('Beta');
  await page.getByRole('status', {name: '加载会话', exact: true}).waitFor();
  // A cached conversation stays visible during its background reconciliation.
  await open('Alpha');
  await pending('Alpha');
  assert.equal(await page.getByText('Alpha answer', {exact: true}).count(), 1);
  assert.equal(await page.getByRole('status', {name: '加载会话', exact: true}).count(), 0);
  await finish('Beta');
  await finish('Alpha');
  assert.equal(await page.getByText('Beta answer', {exact: true}).count(), 0);
  await open('Failure');
  await pending('Failure');
  await app.evaluate(() => {
    const [read] = globalThis.fixtureReads.splice(0, 1);
    read.reject(new Error('Fixture history unavailable'));
  });
  await page.getByText(/Fixture history unavailable/).waitFor();
  assert.equal(await page.getByRole('status', {name: '加载会话', exact: true}).count(), 0);
  assert.equal(await page.evaluate(() => globalThis.welcomeFlashes), 0);
  // Existing-thread navigation must not reread the model configuration.
  assert.equal(await app.evaluate(() => globalThis.fixtureRequests.filter(method => method === 'config.read').length), 1);
  await page.locator('.new-chat').click();
  await page.locator('.welcome').waitFor();
  assert.deepEqual(errors, []);
  console.log('PASS delayed history, cached switching, stale responses, load failure and configuration read boundary');
} finally {
  await app?.close();
  await rm(home, {recursive: true, force: true});
}
