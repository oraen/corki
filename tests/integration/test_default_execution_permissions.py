"""Default CLI/config/SDK authority must reach the real native execution boundary."""

import asyncio
import importlib
import json
import shlex
import sys
from dataclasses import replace

import pytest
from test_bundled_execution import compiler as compiler
from test_execution_policy_live_inheritance import Model, run

from corki.config import CorkiSettings
from corki.config.instructions import ProjectInstructionsConfig
from corki.config.managed_mcp import MCPRequirementsLayer, compose_mcp_requirements
from corki.config.permissions import parse_execution_permissions
from corki.core import LangGraphRuntime
from corki.protocol.items import ToolResultItem


async def exercise(tmp_path, monkeypatch, entry, mode, trust, *, managed=False):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    host_dir = tmp_path / "host-state"
    host_dir.mkdir()
    config = host_dir / "config.toml"
    config.write_text(
        f"[tools]\nmode={json.dumps(mode)}\n[skills]\nenabled=false\n"
        + (
            ""
            if trust is None
            else f"[projects.{json.dumps(str(workspace))}]\ntrust_level={json.dumps(trust)}\n"
        )
    )
    monkeypatch.setenv("CORKI_HOME", str(host_dir))
    monkeypatch.chdir(workspace)
    model = Model(mode)
    requirements = compose_mcp_requirements(
        (MCPRequirementsLayer("host", 'allowed_sandbox_modes=["read-only"]'),) if managed else ()
    )
    if entry == "cli":
        cli = importlib.import_module("corki.cli.main")
        create = LangGraphRuntime.create

        def create_with_model(**kwargs):
            return create(**kwargs, model=model)

        monkeypatch.setattr(cli.LangGraphRuntime, "create", create_with_model)
        monkeypatch.setattr(cli, "load_mcp_requirements", lambda: requirements)
        monkeypatch.setattr(cli, "TerminalUI", lambda *args: object())
        runtime = cli.build_application()._runtime
    else:
        settings = (
            CorkiSettings(
                working_directory=workspace,
                project_instructions=ProjectInstructionsConfig(trust_level=trust),
            )
            if entry == "sdk"
            else CorkiSettings.for_directory(workspace, config_file=config)
        )
        runtime = LangGraphRuntime.create(
            settings=replace(settings, skills_enabled=False, tool_mode=mode),
            model=model,
            database_path=host_dir / "session.db",
            home_path=host_dir,
            mcp_requirements=requirements,
        )
    approvals = []

    async def approve(request):
        approvals.append(request)
        runtime.respond_execution_approval(request.request_id, "accept")

    runtime.set_execution_approval_handler(approve)
    target = workspace / "write-result"
    script = (
        "from pathlib import Path\n"
        "try:\n"
        f" Path({str(target)!r}).write_text('written')\n"
        " print('WRITE_ALLOWED')\n"
        "except PermissionError:\n"
        " print('WRITE_DENIED')\n"
    )
    try:
        await run(runtime, model, {"cmd": shlex.join([sys.executable, "-I", "-c", script])})
        expected_write = trust is not None and not managed
        assert target.exists() is expected_write
        results = [item for item in model.requests[-1].items if isinstance(item, ToolResultItem)]
        marker = "WRITE_ALLOWED" if expected_write else "WRITE_DENIED"
        assert any(marker in item.content for item in results)
        permissions = runtime._settings.execution_permissions
        assert permissions is not None
        assert not permissions.needs_resolution
        assert permissions.profile_json != '{"type":"disabled"}'
        assert json.loads(permissions.approval_policy_json) == (
            "untrusted" if trust == "untrusted" else "on-request"
        )
        assert bool(approvals) is (trust == "untrusted")
        if not managed:
            assert permissions.active_profile.id == (
                ":workspace" if trust is not None else ":read-only"
            )
    finally:
        await runtime.aclose()


@pytest.mark.parametrize("entry", ["sdk", "config", "cli"])
@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("trust", [None, "trusted", "untrusted"])
def test_default_permissions_follow_trust_and_native_execution(
    tmp_path, monkeypatch, compiler, entry, mode, trust
):
    asyncio.run(exercise(tmp_path, monkeypatch, entry, mode, trust))


@pytest.mark.parametrize("entry", ["sdk", "config", "cli"])
def test_default_selection_still_obeys_managed_restrictions(tmp_path, monkeypatch, compiler, entry):
    asyncio.run(exercise(tmp_path, monkeypatch, entry, "direct", "trusted", managed=True))


@pytest.mark.parametrize("entry", ["sdk", "config"])
@pytest.mark.parametrize("forbidden", [False, True])
def test_explicit_full_access_is_native_policy_not_legacy_none(
    tmp_path, monkeypatch, compiler, entry, forbidden
):
    async def scenario():
        host_dir = tmp_path / "host"
        host_dir.mkdir()
        monkeypatch.setenv("CORKI_HOME", str(host_dir))
        config = host_dir / "config.toml"
        config.write_text('default_permissions=":danger-full-access"')
        if forbidden:
            rules = host_dir / "rules"
            rules.mkdir()
            (rules / "deny.rules").write_text(
                'prefix_rule(pattern=["touch"], decision="forbidden")'
            )
        settings = (
            CorkiSettings(
                working_directory=tmp_path,
                execution_permissions=parse_execution_permissions(
                    {"profile": {"type": "disabled"}}, tmp_path, configuration={}
                ),
            )
            if entry == "sdk"
            else CorkiSettings.for_directory(tmp_path, config_file=config)
        )
        model = Model("direct")
        runtime = LangGraphRuntime.create(
            settings=replace(settings, skills_enabled=False),
            model=model,
            database_path=host_dir / "state.db",
            home_path=host_dir,
        )
        target = tmp_path / "created"
        try:
            await run(runtime, model, {"cmd": "touch " + shlex.quote(str(target))})
            assert target.exists() is not forbidden
            assert json.loads(runtime._settings.execution_permissions.profile_json) == {
                "type": "disabled"
            }
            if forbidden:
                results = [
                    item for item in model.requests[-1].items if isinstance(item, ToolResultItem)
                ]
                assert any(item.is_error for item in results)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
