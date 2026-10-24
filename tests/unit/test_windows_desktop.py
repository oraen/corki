"""Adapter contracts; real Windows GUI/console verification is a separate acceptance gate."""

import base64
import subprocess
from pathlib import Path

from corki.platforms import windows_desktop as desktop
from corki.shell import Shell, ShellType


def test_picker_decodes_unicode_and_uses_constant_script(monkeypatch):
    monkeypatch.setattr(desktop, "get_shell", lambda _: Shell(ShellType.POWERSHELL, Path("pwsh")))
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "D:\\中文 目录".encode(), b"")

    monkeypatch.setattr(desktop.subprocess, "run", run)
    assert desktop.choose_directory() == "D:\\中文 目录"
    assert "FolderBrowserDialog" in base64.b64decode(calls[0][-1]).decode("utf-16-le")


def test_launch_passes_paths_as_arguments_not_shell_code(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(desktop.subprocess, "CREATE_NEW_CONSOLE", 0x10, raising=False)
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda *a, **k: calls.append((a, k)))
    folder = tmp_path / "中文 & $(literal)"
    folder.mkdir()
    desktop.launch_cli(folder, tmp_path)
    args, options = calls[0]
    assert args[0][-2:] == ["-m", "corki"]
    assert options["cwd"] == folder
    assert options["env"]["CORKI_HOME"] == str(tmp_path)
    assert options["creationflags"] == 0x10
    assert "shell" not in options


def test_application_prefers_explicit_location(tmp_path, monkeypatch):
    executable = tmp_path / "Corki.exe"
    executable.write_bytes(b"fixture")
    monkeypatch.setenv("CORKI_DESKTOP_EXECUTABLE", str(executable))
    assert desktop.application(tmp_path) == executable


def test_application_uses_matching_packaged_architecture(tmp_path, monkeypatch):
    monkeypatch.delenv("CORKI_DESKTOP_EXECUTABLE", raising=False)
    monkeypatch.setattr(desktop.platform, "machine", lambda: "AMD64")
    for architecture in ("arm64", "x64"):
        executable = tmp_path / "app/out" / f"Corki-win32-{architecture}" / "Corki.exe"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"fixture")
    assert desktop.application(tmp_path) == tmp_path / "app/out/Corki-win32-x64/Corki.exe"
