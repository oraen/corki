"""A physical terminal interrupt cannot abandon startup rollback in progress."""

import os
import sys

import pexpect
import pytest

PROGRAM = r"""
import asyncio, importlib, sys
from pathlib import Path
from corki.config import CorkiSettings
from corki.core import runtime as runtime_module

cli = importlib.import_module("corki.cli.main")
cwd = Path.cwd()
parent = None
cli.CorkiSettings.for_directory = lambda *a, **kw: CorkiSettings(cwd, skills_enabled=False)

def create_model(*args):
    global parent
    parent = asyncio.current_task()
    if sys.argv[1] == "cancel":
        raise asyncio.CancelledError
    raise ValueError("fixture model construction failed")

runtime_module._create_model = create_model
status = cli.main([])
assert status == 130, status
assert (cwd / "home/plugins/fixture/closed").read_text() == "closed"
print("STARTUP_CLEANED_STATUS_130", flush=True)
raise SystemExit(status)
"""

PLUGIN = """
import asyncio
from pathlib import Path
import __main__

def register(api):
    pass

async def aclose():
    print("STARTUP_CLOSE_HELD", flush=True)
    while not __main__.parent.cancelling():
        await asyncio.sleep(0.01)
    print("STARTUP_SIGNAL_OBSERVED", flush=True)
    while not (Path.cwd() / "release-close").exists():
        await asyncio.sleep(0.01)
    Path(__file__).with_name("closed").write_text("closed")
    print("STARTUP_PLUGIN_CLOSED", flush=True)
"""


@pytest.mark.parametrize("width", [40, 100])
@pytest.mark.parametrize("initial_failure", ["error", "cancel"])
def test_physical_ctrl_c_waits_for_startup_plugin_close(tmp_path, width, initial_failure):
    root = tmp_path / "home/plugins/fixture"
    manifest = root / ".codex-plugin/plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"name":"fixture","entrypoint":"plugin.py:register"}')
    (root / "plugin.py").write_text(PLUGIN)
    child = pexpect.spawn(
        sys.executable,
        ["-c", PROGRAM, initial_failure],
        cwd=tmp_path,
        encoding="utf-8",
        dimensions=(30, width),
        timeout=15,
        env={
            **os.environ,
            "CORKI_HOME": str(tmp_path / "home"),
            "TERM": "xterm-256color",
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
        },
    )
    try:
        child.expect_exact("STARTUP_CLOSE_HELD")
        child.sendcontrol("c")
        child.expect_exact("STARTUP_SIGNAL_OBSERVED")
        assert child.isalive()
        assert not (root / "closed").exists()
        (tmp_path / "release-close").write_text("release")
        child.expect_exact("STARTUP_PLUGIN_CLOSED")
        child.expect_exact("STARTUP_CLEANED_STATUS_130")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 130
    finally:
        if child.isalive():
            child.close(force=True)
