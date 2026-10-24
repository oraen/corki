"""Execution data roots come from the host, never a command's working directory."""

from dataclasses import replace
from pathlib import Path

import pytest

from corki.config import CorkiSettings
from corki.config.permissions import ExecutionPermissions, parse_execution_permissions
from corki.core import LangGraphRuntime
from corki.core import runtime as runtime_module


def test_runtime_binds_home_before_registering_tools(tmp_path, monkeypatch):
    policy = ExecutionPermissions(
        tmp_path / "compiler",
        tmp_path / "workspace",
        '{"type":"read-only"}',
        runtime_home=tmp_path / "stale-home",
    )
    settings = CorkiSettings(working_directory=tmp_path, execution_permissions=policy)
    captured = []

    class RegistryReached(Exception):
        pass

    def capture(bound_settings, process_manager):
        captured.append(bound_settings.execution_permissions)
        raise RegistryReached

    monkeypatch.setattr(runtime_module, "_default_registry", capture)
    with pytest.raises(RegistryReached):
        LangGraphRuntime.create(
            settings=settings,
            database_path=tmp_path / "sessions.db",
            home_path=tmp_path / "host-home",
            load_plugins=False,
        )
    assert captured[0].runtime_home == tmp_path / "host-home"
    assert captured[0].policy_cwd == policy.policy_cwd
    assert settings.execution_permissions.runtime_home == tmp_path / "stale-home"
    assert replace(captured[0], profile_json='{"type":"disabled"}').runtime_home == (
        tmp_path / "host-home"
    )


@pytest.mark.parametrize("value", [Path("relative"), "/absolute-but-not-a-path"])
def test_runtime_home_rejects_invalid_host_values(tmp_path, value):
    with pytest.raises(ValueError, match="absolute host path"):
        ExecutionPermissions(
            tmp_path / "compiler", tmp_path, '{"type":"read-only"}', runtime_home=value
        )


def test_user_config_cannot_set_execution_runtime_home(tmp_path):
    with pytest.raises(ValueError, match="execution requires compiler and profile"):
        parse_execution_permissions(
            {
                "compiler": str(tmp_path / "compiler"),
                "profile": {"type": "read-only"},
                "runtime_home": str(tmp_path / "untrusted-home"),
            },
            tmp_path,
        )


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_compiler_protocol_only_sends_host_home_on_windows(tmp_path, monkeypatch, platform):
    import asyncio
    import json
    from types import SimpleNamespace

    from corki.execution import backend

    captured = []

    class RequestCaptured(Exception):
        pass

    async def capture(argv, payload, **kwargs):
        captured.append(json.loads(payload))
        raise RequestCaptured

    monkeypatch.setattr(backend, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(backend, "run_owned", capture)
    policy = ExecutionPermissions(
        tmp_path / "compiler",
        tmp_path / "workspace",
        '{"type":"read-only"}',
        runtime_home=tmp_path / "Corki home",
    )
    with pytest.raises(RequestCaptured):
        asyncio.run(backend._compile(policy, ["program"], tmp_path / "model directory"))
    assert captured[0]["cwd"] == str(tmp_path / "model directory")
    assert captured[0]["policy_cwd"] == str(tmp_path / "workspace")
    if platform == "win32":
        assert captured[0]["runtime_home"] == str(tmp_path / "Corki home")
    else:
        assert "runtime_home" not in captured[0]
