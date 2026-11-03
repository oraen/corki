"""Real Windows console checks for background work, including venv grandchildren."""

import asyncio
import base64
import ctypes
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile
import threading
import time
from ctypes import wintypes
from pathlib import Path

import pytest

from corki.platforms.processes import (
    background_process_options,
    close_job,
    create_subprocess_exec,
)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires real Windows consoles")
REPOSITORY = Path(__file__).resolve().parents[2]


def channel_client(home):
    spec = importlib.util.spec_from_file_location(
        "windows_channel_client", REPOSITORY / "manager/channel_gateway.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ChannelGateway(home)


@pytest.fixture
def no_console_windows():
    """Watch transient windows as well as checking the final process state."""
    api = ctypes.WinDLL("user32")
    api.IsWindowVisible.argtypes = [wintypes.HWND]
    api.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    api.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    visible = set()

    @callback_type
    def visit(window, _):
        name = ctypes.create_unicode_buffer(256)
        api.GetClassNameW(window, name, len(name))
        if name.value == "ConsoleWindowClass" and api.IsWindowVisible(window):
            pid = wintypes.DWORD()
            api.GetWindowThreadProcessId(window, ctypes.byref(pid))
            visible.add(pid.value)
        return True

    api.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    api.EnumWindows(visit, 0)
    baseline = visible.copy()
    done = threading.Event()

    def watch():
        while not done.is_set():
            api.EnumWindows(visit, 0)
            done.wait(.005)

    observer = threading.Thread(target=watch)
    observer.start()
    try:
        yield
    finally:
        done.set()
        observer.join()
        unexpected = visible - baseline
        assert not unexpected, f"Background work opened console windows: {unexpected}"


@pytest.fixture
def python_consoles(tmp_path, monkeypatch):
    """Record the real interpreter's console, after the Windows venv redirector."""
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    records = tmp_path / "consoles"
    records.mkdir()
    (hooks / "sitecustomize.py").write_text(
        "import ctypes,json,os,pathlib\n"
        "api=ctypes.WinDLL('kernel32')\n"
        "api.GetConsoleWindow.restype=ctypes.c_void_p\n"
        "pathlib.Path(os.environ['CORKI_TEST_CONSOLES'],str(os.getpid())+'.json').write_text("
        "json.dumps({'console':bool(api.GetConsoleWindow())}))\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(hooks))
    monkeypatch.setenv("PYTHONPATH", str(hooks) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    monkeypatch.setenv("CORKI_TEST_CONSOLES", str(records))
    yield records
    rows = [json.loads(p.read_text()) for p in records.glob("*.json")]
    assert rows, "The real interpreter console probe did not run"
    assert all(row == {"console": False} for row in rows), rows


def test_owned_process_hides_console_and_preserves_grandchild_io(no_console_windows):
    async def scenario():
        leaf = (
            "import ctypes,json;api=ctypes.WinDLL('kernel32');"
            "api.GetConsoleWindow.restype=ctypes.c_void_p;"
            "print(json.dumps({'console':bool(api.GetConsoleWindow())}))"
        )
        parent = (
            "import subprocess,sys;"
            "subprocess.run([sys.executable,'-c',sys.argv[1]],check=True)"
        )
        process = await create_subprocess_exec(
            sys.executable, "-c", parent, leaf,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 15)
            assert process.returncode == 0, stderr.decode()
            assert json.loads(stdout) == {"console": False}
        finally:
            close_job(process)

    asyncio.run(scenario())


def test_manager_connector_worker_install_and_uninstall_are_hidden(
    tmp_path, python_consoles, no_console_windows,
):
    client = channel_client(tmp_path / "home")
    for action in ("dependencies", "uninstall-dependencies"):
        result = client.request({"action": action, "id": "telegram"})
        token = result["package_jobs"]["telegram"]["token"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = client.request()
            job = result["package_jobs"]["telegram"]
            if job["token"] == token and job["status"] not in ("queued", "running"):
                assert job["status"] == "completed", job
                break
            time.sleep(.05)
        else:
            pytest.fail("Connector package job did not complete")
    assert len(list(python_consoles.glob("*.json"))) >= 4


def test_channel_service_starts_and_stops_without_console(
    tmp_path, python_consoles, no_console_windows,
):
    client = channel_client(tmp_path / "home")
    try:
        assert client.request({"action": "power", "enabled": True})["running"]
        assert client.request()["running"]
    finally:
        assert not client.request({"action": "power", "enabled": False})["running"]


def test_npm_connector_install_runs_real_batch_helper_without_windows(
    tmp_path, monkeypatch, no_console_windows,
):
    monkeypatch.syspath_prepend(str(REPOSITORY / "gateway/src"))
    from corki_gateway import plugins
    from corki_gateway.store import Store

    if not plugins.find("npm"):
        pytest.skip("requires Node/npm")
    # A complete local tarball and lockfile exercise real npm ci without network.
    archive = tmp_path / "fixture.tgz"
    package = json.dumps({"name": "corki-console-fixture", "version": "1.0.0"}).encode()
    with tarfile.open(archive, "w:gz") as output:
        info = tarfile.TarInfo("package/package.json")
        info.size = len(package)
        output.addfile(info, io.BytesIO(package))
    integrity = "sha512-" + base64.b64encode(hashlib.sha512(archive.read_bytes()).digest()).decode()
    assets = tmp_path / "assets"
    assets.mkdir()
    dependency = {"corki-console-fixture": archive.as_uri()}
    manifest = {"name": "corki-install-fixture", "version": "1.0.0", "dependencies": dependency}
    (assets / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
    (assets / "package-lock.json").write_text(json.dumps({
        "name": manifest["name"], "version": "1.0.0", "lockfileVersion": 3,
        "requires": True, "packages": {
            "": manifest,
            "node_modules/corki-console-fixture": {
                "version": "1.0.0", "resolved": archive.as_uri(), "integrity": integrity,
            },
        },
    }), encoding="utf-8")
    monkeypatch.setattr(plugins, "package_assets", lambda identity: assets)
    store = Store(tmp_path / "home")
    plugins.install_dependencies(store, "telegram")
    installed = store.root / "node/telegram"
    assert (installed / ".installed").is_file()
    assert (installed / "node_modules/corki-console-fixture/package.json").is_file()


def test_cron_worker_is_hidden_after_scheduler_releases_it(
    tmp_path, python_consoles, no_console_windows,
):
    from corki.cron.service import CronService

    service = CronService(tmp_path / "home")
    service._launch("missing-test-run")
    service.close()  # Stopping the scheduler must not kill its independent worker.
    service.children[0].wait(timeout=15)


def test_sync_background_options_preserve_piped_output(no_console_windows):
    result = subprocess.run(
        [sys.executable, "-c", "print('background output')"],
        capture_output=True, text=True, check=True, timeout=10,
        **background_process_options(),
    )
    assert result.stdout.strip() == "background output"


def test_code_mode_worker_is_hidden(no_console_windows):
    from corki.code_mode.service import CodeModeService
    from corki.tools import ToolRegistry

    if not CodeModeService.available():
        pytest.skip("requires corki[code-mode]")

    async def scenario():
        service = CodeModeService(ToolRegistry())
        try:
            result = await service.execute(
                "hidden-worker", "text('worker completed');", 10000, 1000,
            )
            assert "worker completed" in result.content
        finally:
            await service.aclose()

    asyncio.run(scenario())
