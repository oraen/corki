import { python } from './platform.mjs';
import { _electron as electron } from 'playwright';
import { mkdtemp } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';

const home = await mkdtemp(path.join(os.tmpdir(), 'corki-activation-'));
const env = { ...process.env, CORKI_HOME: home, CORKI_PYTHON: python };
delete env.ELECTRON_RUN_AS_NODE;
const executablePath = process.env.CORKI_TEST_EXECUTABLE;
const args = executablePath ? [] : [path.resolve('.')];
const app = await electron.launch({ executablePath, args, env });
try {
  const page = await app.firstWindow();
  await page.getByRole('textbox', { name: '消息', exact: true }).waitFor();
  const visible = () => app.evaluate(({ BrowserWindow }) => {
    const window = BrowserWindow.getAllWindows()[0];
    return window.isVisible() && !window.isMinimized();
  });
  async function assertVisible() {
    const deadline = Date.now() + 5000;
    while (!(await visible()) && Date.now() < deadline)
      await new Promise(resolve => setTimeout(resolve, 50));
    assert.equal(await visible(), true);
  }
  await assertVisible();
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].hide());
  assert.equal(await visible(), false);
  const executable = await app.evaluate(() => process.execPath);
  await new Promise((resolve, reject) => {
    const child = spawn(executable, args, { env, stdio: 'ignore', windowsHide: true });
    child.on('error', reject);
    child.on('exit', code => code === 0 ? resolve() : reject(new Error(`Second launch: ${code}`)));
  });
  await assertVisible();
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].minimize());
  await app.evaluate(({ app }) => app.emit('activate'));
  await assertVisible();
  assert.equal(await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().length), 1);
  console.log('Desktop startup, hidden-window relaunch and minimized-window activation passed.');
} finally {
  await app.close();
}
