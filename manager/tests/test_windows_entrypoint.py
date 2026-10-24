"""Run the real PowerShell entrypoint on Windows, without starting user services."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="requires real Windows")
SCRIPT = Path(__file__).resolve().parents[1] / "bin" / "start.ps1"


def run_script(tmp_path, python, *args):
    work = tmp_path / "工作 目录"
    work.mkdir(exist_ok=True)
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
         *args],
        cwd=work,
        env={**os.environ, "CORKI_MANAGER_PYTHON": str(python), "CORKI_HOME": str(work)},
        capture_output=True,
        # Include cold PowerShell and Python imports on emulated Windows hosts.
        timeout=120,
    )


def test_windows_entrypoint_accepts_arguments_from_other_directory(tmp_path):
    result = run_script(tmp_path, sys.executable, "--home", str(tmp_path / "新 home"), "--help")
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert b"--workspace" in result.stdout and b"--port" in result.stdout
    assert not (tmp_path / "新 home").exists()


def test_windows_entrypoint_rejects_missing_python(tmp_path):
    result = run_script(tmp_path, tmp_path / "missing python.exe", "--help")
    assert result.returncode != 0
    assert b"CORKI_MANAGER_PYTHON" in result.stderr
