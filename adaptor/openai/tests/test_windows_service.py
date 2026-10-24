"""Windows lifecycle tests use isolated credentials and never contact upstream."""

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from corki_openai_gateway.windows_service import gateway_arguments, state_directory


def test_arguments_follow_explicit_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("CORKI_HOME", str(tmp_path / "应用 空间"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "login"))
    monkeypatch.setenv("CORKI_GATEWAY_PORT", "17501")
    monkeypatch.delenv("CORKI_GATEWAY_AUTH_FILE", raising=False)
    monkeypatch.delenv("CORKI_GATEWAY_KEY_FILE", raising=False)
    assert state_directory() == tmp_path / "应用 空间/gateway"
    assert gateway_arguments() == [
        "--port", "17501", "--key-file", str(state_directory() / "local.key"),
        "--auth-file", str(tmp_path / "login/auth.json"),
    ]


@pytest.mark.skipif(os.name != "nt", reason="requires real Windows kernel objects")
@pytest.mark.parametrize("entrypoint", ["module", "powershell", "powershell-default"])
def test_start_twice_stop_twice_without_upstream(tmp_path, entrypoint):
    home = tmp_path / "应用 空间"
    home.mkdir()
    key, auth = home / "local.key", home / "auth.json"
    key.write_text("synthetic-local-key", encoding="utf-8")
    auth.write_text(json.dumps({
        "auth_mode": "chatgpt",
        "fixture_note": "中文路径与配置读取测试",
        "tokens": {"access_token": "synthetic-test-token", "account_id": "test-account"},
    }, ensure_ascii=False), encoding="utf-8")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, CORKI_HOME=str(home), CORKI_GATEWAY_PORT=str(port),
               CORKI_GATEWAY_KEY_FILE=str(key), CORKI_GATEWAY_AUTH_FILE=str(auth),
               CORKI_GATEWAY_PYTHON=sys.executable)
    scripts = home / "启动 脚本"
    if entrypoint == "powershell-default":
        project = Path(__file__).resolve().parents[1]
        if not (project / ".venv/Scripts/python.exe").is_file():
            pytest.skip("requires gateway installed with install.ps1")
        scripts = project / "bin"
        env.pop("CORKI_GATEWAY_PYTHON", None)
    elif entrypoint == "powershell":
        scripts.mkdir()
        source = Path(__file__).resolve().parents[1] / "bin"
        for action in ("start", "stop"):
            (scripts / f"{action}.ps1").write_bytes((source / f"{action}.ps1").read_bytes())

    def command(action):
        argv = [sys.executable, "-m", "corki_openai_gateway.windows_service", action]
        if entrypoint.startswith("powershell"):
            argv = [
                "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-File", str(scripts / f"{action}.ps1"),
            ]
        return subprocess.run(
            # PowerShell cold startup can be slow on an emulated Windows host.
            # The service's own 30-second readiness timeout remains unchanged.
            argv, cwd=home, env=env, capture_output=True, text=True, timeout=120,
        )

    try:
        first = command("start")
        assert first.returncode == 0, first.stderr
        again = command("start")
        assert again.returncode == 0, again.stderr
        with httpx.Client(trust_env=False, timeout=3) as client:
            response = client.post(f"http://127.0.0.1:{port}/v1/responses", json={})
            assert response.status_code == 401
        stopped = command("stop")
        assert stopped.returncode == 0, stopped.stderr
        repeated = command("stop")
        assert repeated.returncode == 0, repeated.stderr
    finally:
        command("stop")
