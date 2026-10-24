import os
import subprocess

import pytest

from corki.shell import POWERSHELL_UTF8_PREFIX, ShellType, get_shell, powershell_utf8_argv


def test_utf8_prelude_is_idempotent_and_preserves_user_script():
    command = ['pwsh', '-NoProfile', '-Command', 'Write-Output "中文"']
    result = powershell_utf8_argv(command)
    assert result[:-1] == command[:-1]
    assert result[-1] == POWERSHELL_UTF8_PREFIX + command[-1]
    assert command[-1] == 'Write-Output "中文"'
    assert powershell_utf8_argv(result) == result


@pytest.mark.parametrize('command', [
    ['cmd', '/c', 'echo hello'], ['pwsh', '-File', 'file.ps1'],
    ['pwsh', '-EncodedCommand', 'opaque'], ['pwsh', '-Command', 'script', 'argument'],
])
def test_other_shell_forms_are_not_rewritten(command):
    assert powershell_utf8_argv(command) == command


@pytest.mark.skipif(os.name != 'nt', reason='requires native Windows PowerShell')
def test_windows_powershell_emits_utf8_bytes():
    shell = get_shell(ShellType.POWERSHELL)
    assert shell is not None
    script = "[Console]::WriteLine('你好 世界')"
    result = subprocess.run(
        powershell_utf8_argv(shell.exec_args(script, login=False)),
        capture_output=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr.decode('utf-8', errors='replace')
    assert result.stdout.decode('utf-8-sig').strip() == '你好 世界'
