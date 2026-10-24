import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn, execFileSync} from 'node:child_process';
import {once} from 'node:events';
import path from 'node:path';
import {childProcesses, fixtureCommand} from './platform.mjs';

test('fixture command preserves quotes, Unicode and shell metacharacters', () => {
  const expected = "中文 空格 'quoted' \"double\" & | %PATH% $value (x)";
  const command = fixtureCommand(process.execPath, ['-e', 'process.stdout.write(process.argv[1])', expected]);
  const windows = process.platform === 'win32';
  const shell = windows ? path.join(process.env.SystemRoot || 'C:\\Windows',
    'System32/WindowsPowerShell/v1.0/powershell.exe') : '/bin/sh';
  const args = windows
    ? ['-NoProfile', '-Command', '[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; '+command.cmd]
    : ['-c', command.cmd];
  assert.equal(execFileSync(shell, args, {encoding:'utf8', timeout:120000}), expected);
});

test('ordinary fixture invocation works in Windows constrained language', {skip:process.platform!=='win32'}, () => {
  const expected="中文 空格 'quoted' & | %PATH% $value (x)";
  const command=fixtureCommand(process.execPath,['-e','process.stdout.write(process.argv[1])',expected]);
  const shell=path.join(process.env.SystemRoot||'C:\\Windows','System32/WindowsPowerShell/v1.0/powershell.exe');
  const script="[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; $ExecutionContext.SessionState.LanguageMode='ConstrainedLanguage'; "+command.cmd;
  assert.equal(execFileSync(shell,['-NoProfile','-Command',script],{encoding:'utf8',timeout:120000}),expected);
});

test('process lookup identifies only children of the requested parent', async () => {
  const marker = 'corki-process-ownership-fixture';
  const child = spawn(process.execPath, ['-e', 'console.log("ready");setInterval(()=>{},1000)', marker],
    {stdio:['ignore','pipe','pipe']});
  try {
    await once(child.stdout, 'data');
    const rows = childProcesses(process.pid);
    assert(rows.some(row => row.pid === child.pid && row.command.includes(marker)));
    assert(!childProcesses(child.pid).some(row => row.pid === process.pid));
    assert.throws(() => childProcesses('1; invalid'), /Invalid test process ID/);
  } finally {
    const exited = once(child, 'exit');
    child.kill();
    await exited;
  }
});
