"""Managed lifecycle against real local children; no upstream calls or real login."""

import json
import os
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from corki_openai_gateway.control import LOCAL_KEY, Controller
from corki_openai_gateway.gateway import CredentialsUnavailable


@pytest.fixture
def controller(tmp_path, monkeypatch):
    auth = tmp_path / "auth.json"
    auth.write_text(
        json.dumps({"tokens": {"access_token": "fixture-token", "account_id": "fixture-account"}})
    )
    monkeypatch.setenv("CORKI_GATEWAY_AUTH_FILE", str(auth))
    return Controller(tmp_path / "home")


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.mark.skipif(os.name != "nt", reason="requires Windows console process semantics")
def test_managed_gateway_has_no_console(controller, monkeypatch, tmp_path):
    """Exercise the real venv launcher: detaching can override the hidden flag."""
    state = tmp_path / "console.json"
    popen = subprocess.Popen

    def launch(args, **kwargs):
        if args[1:3] == ["-m", "corki_openai_gateway"]:
            probe = (
                "import ctypes,json,pathlib,runpy; "
                "api=ctypes.WinDLL('kernel32'); "
                "api.GetConsoleWindow.restype=ctypes.c_void_p; "
                f"pathlib.Path({str(state)!r}).write_text("
                "json.dumps({'console':bool(api.GetConsoleWindow())})); "
                "runpy.run_module('corki_openai_gateway',run_name='__main__')"
            )
            args = [args[0], "-c", probe, *args[3:]]
        return popen(args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", launch)
    try:
        assert controller.start(free_port())["running"]
        assert json.loads(state.read_text()) == {"console": False}
    finally:
        controller.stop()


def test_real_start_restart_stop_and_private_metadata(controller):
    first, second = free_port(), free_port()
    try:
        assert controller.start(first)["running"]
        initial = controller.metadata()
        assert (controller.root / "local.key").read_text() == LOCAL_KEY
        assert initial["port"] == first and controller.status(first)["managed"]
        assert "fixture-token" not in (controller.root / "service.json").read_text()
        assert controller.start(first)["instance"] == initial["instance"]
        assert controller.start(second)["running"]
        assert not controller.status(first)["running"] and not controller.status(first)["occupied"]
        assert controller.metadata()["port"] == second
        if os.name != "nt":
            assert (controller.root / "service.control").stat().st_mode & 0o777 == 0o600
    finally:
        controller.stop()
    assert not controller.status(second)["running"] and not controller.status(second)["occupied"]


def test_missing_login_does_not_start_listener(controller, monkeypatch, tmp_path):
    monkeypatch.setenv("CORKI_GATEWAY_AUTH_FILE", str(tmp_path / "missing.json"))
    port = free_port()
    with pytest.raises(CredentialsUnavailable):
        controller.start(port)
    assert not controller.status(port)["running"]


def test_foreign_listener_is_never_stopped(controller):
    class Foreign(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"foreign")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Foreign)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        port = server.server_port
        with pytest.raises(ValueError, match="占用"):
            controller.start(port)
        assert controller.status(port)["occupied"]
        controller.root.mkdir(parents=True, exist_ok=True)
        (controller.root / "service.json").write_text(json.dumps({"port": port}))
        with pytest.raises(ValueError):
            controller.stop()
        assert controller.status(port)["occupied"]
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_cli_start_reuses_manager_port_without_explicit_override(controller, monkeypatch, capsys):
    from corki_openai_gateway.control import main

    controller.root.mkdir(parents=True, exist_ok=True)
    (controller.root / "manager.json").write_text(json.dumps({"port": 18234}))
    ports = []

    def start(self, port):
        ports.append(port)
        return {"running": True, "port": port}

    monkeypatch.setattr(Controller, "start", start)
    assert main(["start", "--home", str(controller.home)]) == 0
    assert ports == [18234]
    assert main(["start", "--home", str(controller.home), "--port", "18235"]) == 0
    assert ports == [18234, 18235]


def test_migrated_login_job_is_scoped_and_disabled_before_stop(controller, tmp_path, monkeypatch):
    import plistlib
    from types import SimpleNamespace

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    controller.root.mkdir(parents=True, exist_ok=True)
    archive = {
        "RunAtLoad": True,
        "KeepAlive": True,
        "EnvironmentVariables": {
            "HTTPS_PROXY": "http://proxy.example.invalid",
            "LOGIN_TOKEN": "fixture-private",
        },
    }
    (controller.root / "legacy-service.plist").write_bytes(plistlib.dumps(archive))
    # Exercise the native persistence format on macOS without loading any job.
    import corki_openai_gateway.control as module

    monkeypatch.setattr(module.sys, "platform", "darwin")
    controller.login_job(18236, controller.auth_file(), enabled=True)
    plist = next((tmp_path / "Library/LaunchAgents").glob("*.plist"))
    value = plistlib.loads(plist.read_bytes())
    assert value["RunAtLoad"] and value["KeepAlive"]
    assert "18236" in value["ProgramArguments"] and controller.owner in value["ProgramArguments"]
    assert "LOGIN_TOKEN" not in value["EnvironmentVariables"]
    monkeypatch.setattr(
        module.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1)
    )
    monkeypatch.setattr(module.os, "getuid", lambda: 123, raising=False)
    controller.stop_login_job()
    value = plistlib.loads(plist.read_bytes())
    assert value["RunAtLoad"] is False and value["KeepAlive"] is False


@pytest.mark.parametrize(
    "tokens",
    [None, {}, {"access_token": "", "account_id": "fixture"}, {"access_token": "fixture-token"}],
)
def test_enable_reports_missing_login_without_starting_gateway(
    controller, monkeypatch, capsys, tokens
):
    from corki_openai_gateway.control import main

    auth = controller.root.parent / "missing-login.json"
    auth.parent.mkdir(parents=True, exist_ok=True)
    if tokens is not None:
        auth.write_text(json.dumps({"tokens": tokens}))
    monkeypatch.setenv("CORKI_GATEWAY_AUTH_FILE", str(auth))
    assert main(["check", "--home", str(controller.home)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["error"] == "开启失败，没有检测到对应登录信息"
    assert result["code"] == "credentials_unavailable"
    assert not controller.metadata()


def test_auto_start_prefers_standard_port_without_touching_existing_listeners(
    controller, monkeypatch
):
    monkeypatch.delenv("CORKI_GATEWAY_PORT", raising=False)
    monkeypatch.setattr(controller, "status", lambda *args: {"running": False})
    calls = []

    def start(port, *, auto_port=False):
        calls.append((port, auto_port))
        return {"running": True, "port": port}

    monkeypatch.setattr(controller, "start", start)
    controller.start_auto()
    assert calls == [(17001, True)]


def test_auto_start_binds_free_port_when_preferred_is_occupied(controller, monkeypatch):
    class Foreign(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"foreign-fixture")

        def log_message(self, *args):
            pass

    foreign = ThreadingHTTPServer(("127.0.0.1", 0), Foreign)
    worker = threading.Thread(target=foreign.serve_forever)
    worker.start()
    monkeypatch.setenv("CORKI_GATEWAY_PORT", str(foreign.server_port))
    try:
        result = controller.start_auto()
        assert result["running"] and result["managed"]
        assert result["port"] != foreign.server_port
        assert controller.status(foreign.server_port)["occupied"]
        # A second enable reuses the current instance instead of moving ports.
        assert controller.start_auto()["instance"] == result["instance"]
        assert not list(controller.root.glob("service-ready-*.json"))
        controller.stop()
        assert not controller.status(result["port"])["running"]
        assert controller.status(foreign.server_port)["occupied"]
    finally:
        controller.stop()
        foreign.shutdown()
        foreign.server_close()
        worker.join()
