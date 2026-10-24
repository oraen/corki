"""Windows desktop entry points. User paths travel as arguments, never shell source."""

import base64
import os
import platform
import subprocess
import sys
from pathlib import Path

from corki.shell import ShellType, get_shell


def choose_directory() -> str | None:
    shell = get_shell(ShellType.POWERSHELL)
    if shell is None:
        raise ValueError("未找到 PowerShell，请手动输入工作目录")
    script = """$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'Corki'
try {
    if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        [Console]::Write($dialog.SelectedPath)
    }
} finally { $dialog.Dispose() }
"""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        result = subprocess.run(
            [str(shell.path), "-NoProfile", "-STA", "-EncodedCommand", encoded],
            capture_output=True, timeout=120, check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError("目录选择超时，请重试或手动输入工作目录") from error
    except OSError as error:
        raise ValueError("无法打开目录选择窗口，请手动输入工作目录") from error
    if result.returncode:
        raise ValueError("无法打开目录选择窗口，请手动输入工作目录")
    return result.stdout.decode("utf-8-sig").strip() or None


def application(repository: Path) -> Path:
    candidates = []
    configured = os.environ.get("CORKI_DESKTOP_EXECUTABLE")
    if configured:
        selected = Path(configured).expanduser()
        if not selected.is_absolute() or not selected.is_file():
            raise ValueError("CORKI_DESKTOP_EXECUTABLE 必须指向桌面应用的完整路径")
        return selected
    architecture = {
        "amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64",
        "x86": "ia32", "i386": "ia32", "i686": "ia32",
    }.get(platform.machine().lower())
    if architecture:
        candidates.append(repository / "app/out" / f"Corki-win32-{architecture}" / "Corki.exe")
    for variable in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
        value = os.environ.get(variable)
        if value:
            candidates.extend((Path(value) / "Programs/Corki/Corki.exe",
                               Path(value) / "Corki/Corki.exe"))
    # Never resolve Corki.exe from PATH: pip's CLI launcher has that same name.
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ValueError("未找到 Corki 桌面应用，请先安装或打包桌面应用")


def launch_cli(directory: Path, home: Path) -> None:
    python = Path(sys.executable)
    if python.name.lower() == "pythonw.exe":
        python = python.with_name("python.exe")
    env = os.environ.copy()
    env["CORKI_HOME"] = str(home)
    # The manager's interpreter already has Corki installed. CREATE_NEW_CONSOLE
    # uses the user's system console host without requiring Windows Terminal.
    try:
        subprocess.Popen(
            [str(python), "-m", "corki"], cwd=directory, env=env,
            creationflags=subprocess.CREATE_NEW_CONSOLE, close_fds=True,
        )
    except OSError as error:
        raise ValueError("无法启动 Corki 终端，请检查 Python 安装") from error
