import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';

export const repository = fileURLToPath(new URL('../../', import.meta.url));
export const browserLaunchOptions = {
  headless: true,
  ...(process.env.CORKI_TEST_BROWSER
    ? { executablePath: path.resolve(process.env.CORKI_TEST_BROWSER) }
    : {}),
};
export const python = process.env.CORKI_TEST_PYTHON
  ? path.resolve(process.env.CORKI_TEST_PYTHON)
  : path.join(repository, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
export const systemPath = process.platform === 'win32'
  ? [process.env.SystemRoot, path.join(process.env.SystemRoot || 'C:\\Windows', 'System32')]
      .filter(Boolean).join(path.delimiter)
  : '/usr/bin:/bin';

export function fixtureCommand(program, args = []) {
  const windows = process.platform === 'win32';
  const quote = value => "'" + value.replaceAll("'", windows ? "''" : "'\\''") + "'";
  // Ordinary file/argument calls also work in sandbox ConstrainedLanguage.
  // Windows PowerShell 5.1 drops embedded quotes/empty native arguments; the
  // explicit CRT fallback below is only suitable for full-language host tests.
  const direct = args.every(value => value.length > 0 && !value.includes('"'));
  const crtQuote = value => '"' + value.replace(/(\\*)"/g, '$1$1\\"')
    .replace(/(\\+)$/, '$1$1') + '"';
  return {
    cmd: windows
      ? direct ? '& ' + [program, ...args].map(quote).join(' ')
      : '$s=New-Object System.Diagnostics.ProcessStartInfo; ' +
        '$s.FileName='+quote(program)+'; $s.Arguments='+quote(args.map(crtQuote).join(' '))+'; ' +
        '$s.UseShellExecute=$false; $p=[System.Diagnostics.Process]::Start($s); $p.WaitForExit(); exit $p.ExitCode'
      : [program, ...args].map(quote).join(' '),
    shell: windows ? 'powershell' : '/bin/sh',
    login: false,
  };
}

export function childProcesses(parent) {
  assertPid(parent);
  if (process.platform === 'win32') {
    const powershell = path.join(process.env.SystemRoot || 'C:\\Windows',
      'System32/WindowsPowerShell/v1.0/powershell.exe');
    const output = execFileSync(powershell, ['-NoProfile', '-Command',
      '[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; ' +
      `ConvertTo-Json -Compress -InputObject @(Get-CimInstance Win32_Process -Filter "ParentProcessId = ${parent}" | Select-Object ProcessId,CommandLine)`],
      { encoding: 'utf8', timeout: 120000 });
    return JSON.parse(output).map(row => ({pid: row.ProcessId, command: row.CommandLine || ''}));
  }
  return execFileSync('/bin/ps', ['-axo', 'pid=,ppid=,command='], {encoding:'utf8'})
    .split('\n').map(line => line.trim().match(/^(\d+)\s+(\d+)\s+(.*)$/))
    .filter(row => row && Number(row[2]) === parent)
    .map(row => ({pid:Number(row[1]), command:row[3]}));
}

function assertPid(pid) {
  if (!Number.isSafeInteger(pid) || pid <= 0) throw new Error('Invalid test process ID');
}
