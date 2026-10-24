import { python } from './platform.mjs';
import { _electron as electron } from 'playwright';
import assert from 'node:assert/strict';
import { mkdtemp } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';

const home = await mkdtemp(path.join(os.tmpdir(), 'corki-desktop-ipc-'));
const env = { ...process.env, CORKI_HOME: home, CORKI_PYTHON: python };
delete env.ELECTRON_RUN_AS_NODE;
let app;
try {
  app = await electron.launch({ args: [path.resolve('.')], env });
  const page = await app.firstWindow();
  await page.waitForFunction(() => !!window.corki);
  const isolation = await page.evaluate(() => ({ node: typeof window.require, process: typeof window.process, ipc: typeof window.ipcRenderer }));
  assert.deepEqual(isolation, { node: 'undefined', process: 'undefined', ipc: 'undefined' });
  const denied = await page.evaluate(async () => {
    const results = [];
    for (const operation of [
      () => window.corki.request('execute-shell', { cmd: 'irrelevant' }),
      () => window.corki.request('status', null),
      () => window.corki.request('status', []),
      () => window.corki.request('status', { large: 'x'.repeat(3_000_000) }),
      () => window.corki.copy({ text: 'invalid' }),
      () => window.corki.openLink('file:///tmp/invalid'),
      () => window.corki.openLink('javascript:alert(1)'),
      () => window.corki.openLink('https://user:password@example.invalid'),
      () => window.corki.request('state.write', { key: '../../settings', value: {} }),
      () => window.corki.request('assets.read', { assetId: '../../settings.json' }),
    ]) {
      try { await operation(); results.push('ACCEPTED'); }
      catch (error) { results.push(error.message); }
    }
    return results;
  });
  assert(!denied.includes('ACCEPTED'), JSON.stringify(denied));
  // Prove disposal removes exactly this subscription without touching others.
  await page.evaluate(() => {
    window.fixtureEvents = [];
    const dispose = window.corki.subscribe(() => window.fixtureEvents.push('removed'));
    dispose(); dispose();
    window.fixtureDispose = window.corki.subscribe(event => {
      if (event.type === 'fixture.probe') window.fixtureEvents.push('active');
    });
  });
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].webContents.send('corki:event', { type: 'fixture.probe', instance: 'fixture', sequence: 0, data: {} }));
  await page.waitForFunction(() => window.fixtureEvents.includes('active'));
  assert.deepEqual(await page.evaluate(() => window.fixtureEvents), ['active']);
  await page.evaluate(() => window.fixtureDispose());
  // A second native window with the SAME origin and preload is not an owner.
  const rejectedSender = await app.evaluate(async ({ BrowserWindow }, preload) => {
    const owner = BrowserWindow.getAllWindows()[0];
    const attacker = new BrowserWindow({ show: false, webPreferences: {
      preload,
      sandbox: true, contextIsolation: true, nodeIntegration: false,
    } });
    try {
      await attacker.loadURL(owner.webContents.getURL());
      return await attacker.webContents.executeJavaScript("window.corki.request('status').then(() => 'ACCEPTED', e => e.message)");
    } finally { attacker.destroy(); }
  }, path.resolve("dist/preload.cjs"));
  assert.match(rejectedSender, /Untrusted sender/);
  assert.equal((await page.evaluate(() => window.corki.request('status'))).running.length, 0);
  await page.evaluate(() => window.open('https://example.invalid'));
  assert.equal(await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().length), 1);
  console.log('PASS native sandbox isolation, method/argument/size/path/link rejection, subscription disposal, same-origin foreign-window IPC rejection, popup denial and surviving legitimate host');
} finally {
  if (app) await app.close();
  console.log('Disposable test home:', home);
}
