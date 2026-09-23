"""Managed authority enters Runtime admission, not model-selected tool arguments."""

import asyncio
import json
import os
import shlex
import sys
from pathlib import Path

import pytest

from corki.config import CorkiSettings
from corki.config.managed_mcp import MCPRequirementsLayer, load_mcp_requirements
from corki.config.permissions import ExecutionPermissions
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall


def engine():
    path = os.environ.get("CORKI_TEST_SANDBOX_COMPILER")
    if not path or sys.platform != "darwin":
        pytest.skip("requires built native compiler and macOS Seatbelt")
    return Path(path)


async def run(tmp_path, profile, requirements, script, *, nested=False):
    requests = []

    class Model:
        async def stream(self, request):
            requests.append(request)
            turn, step = request.items[-1].turn_id, new_step_id()
            args = {
                "cmd": shlex.join([sys.executable, "-I", "-c", script]),
                "workdir": str(tmp_path),
                "login": False,
                "yield_time_ms": 1000,
            }
            if len(requests) == 1:
                call = (
                    ToolCall(new_tool_call_id(), "exec_command", args)
                    if not nested
                    else ToolCall(
                        new_tool_call_id(),
                        "exec",
                        None,
                        raw_arguments="text(await tools.exec_command(" + json.dumps(args) + "));",
                        input_kind="freeform",
                    )
                )
                item = ToolCallItem(call, turn, step)
            else:
                item = AssistantMessageItem("done", turn, step)
            yield ModelCompleted((item,))

        async def aclose(self):
            pass

    runtime = await LangGraphRuntime.acreate(
        settings=CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            tool_mode="code_mode_only" if nested else "direct",
            execution_permissions=ExecutionPermissions(engine(), tmp_path, json.dumps(profile)),
        ),
        model=Model(),
        database_path=tmp_path / "sessions.db",
        mcp_requirements=requirements,
    )
    try:
        events = [event async for event in runtime.stream("exercise managed authority")]
        assert isinstance(events[-1], TurnCompleted), events[-1]
        assert len(requests) == 2
        return [item for item in requests[-1].items if isinstance(item, ToolResultItem)]
    finally:
        await runtime.aclose()


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("pattern", ["./private.txt", "./*.txt"])
def test_layered_deny_read_survives_empty_overlay_and_model_cwd(tmp_path, nested, pattern):
    engine()
    managed = tmp_path / "managed"
    managed.mkdir()
    secret = managed / "private.txt"
    secret.write_text("not-for-the-model")
    decoy = tmp_path / "private.txt"
    decoy.write_text("public")
    alias = tmp_path / "alias"
    alias.symlink_to(secret)
    path = managed / "requirements.toml"
    path.write_text(f"[permissions.filesystem]\ndeny_read=[{json.dumps(pattern)}]")
    snapshot = load_mcp_requirements(
        system_path=path,
        layers=(MCPRequirementsLayer("higher", "[permissions.filesystem]\ndeny_read=[]"),),
    )
    # The immutable captured source must not be replaced by a later disk read.
    path.write_text("[permissions.filesystem]\ndeny_read=[]")
    script = f"""
from pathlib import Path
print(Path({str(decoy)!r}).read_text())
try:
    print(Path({str(secret)!r}).read_text())
except OSError:
    print('MANAGED_DENIED')
try:
    print(Path({str(alias)!r}).read_text())
except OSError:
    print('ALIAS_DENIED')
"""
    results = asyncio.run(
        run(tmp_path, {"type": "workspace-write"}, snapshot, script, nested=nested)
    )
    assert any("MANAGED_DENIED" in item.content and "public" in item.content for item in results)
    assert any("ALIAS_DENIED" in item.content for item in results)
    assert all("not-for-the-model" not in item.content for item in results)


def test_disallowed_workspace_falls_back_before_execution(tmp_path):
    engine()
    path = tmp_path / "requirements.toml"
    path.write_text('allowed_sandbox_modes=["read-only"]')
    snapshot = load_mcp_requirements(system_path=path)
    output = tmp_path / "must-not-exist"
    script = f"""
from pathlib import Path
try:
    Path({str(output)!r}).write_text('escape')
except OSError:
    print('READ_ONLY_FALLBACK')
"""
    results = asyncio.run(run(tmp_path, {"type": "workspace-write"}, snapshot, script))
    assert not output.exists()
    assert any("READ_ONLY_FALLBACK" in item.content for item in results)


@pytest.mark.parametrize(
    ("profile", "requirements", "message"),
    [
        ({"type": "disabled"}, 'allowed_sandbox_modes=["read-only"]', "approval_policy=never"),
        (
            {
                "type": "managed",
                "file_system": {"type": "unrestricted"},
                "network": "restricted",
            },
            'allowed_sandbox_modes=["read-only","workspace-write"]',
            "approval_policy=never",
        ),
        (
            {"type": "workspace-write"},
            'allowed_sandbox_modes=["workspace-write"]',
            "must include 'read-only'",
        ),
        (
            {"type": "read-only"},
            'allowed_sandbox_modes=["invalid"]',
            "unknown variant",
        ),
    ],
)
def test_invalid_managed_selection_fails_before_mcp_and_model(
    tmp_path, monkeypatch, profile, requirements, message
):
    compiler = engine()
    from corki.mcp import MCPManager

    calls = []

    async def start(_):
        calls.append("mcp")

    monkeypatch.setattr(MCPManager, "start_session", start)
    path = tmp_path / "requirements.toml"
    path.write_text(requirements)
    snapshot = load_mcp_requirements(system_path=path)

    async def scenario():
        class Model:
            async def stream(self, request):
                calls.append("model")
                yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                execution_permissions=ExecutionPermissions(compiler, tmp_path, json.dumps(profile)),
            ),
            model=Model(),
            database_path=tmp_path / "sessions.db",
            mcp_requirements=snapshot,
        )
        try:
            with pytest.raises(ValueError, match=message):
                _ = [event async for event in runtime.stream("must not sample")]
            assert not runtime._thread_created
            assert not calls
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_requirements_cannot_run_without_execution_backend(tmp_path):
    path = tmp_path / "requirements.toml"
    path.write_text('allowed_sandbox_modes=["read-only"]')
    with pytest.raises(ValueError, match="configured sandbox compiler"):
        LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path, execution_permissions=None),
            database_path=tmp_path / "not-created.db",
            mcp_requirements=load_mcp_requirements(system_path=path),
        )
    assert not (tmp_path / "not-created.db").exists()


def test_explicit_readable_root_cannot_override_managed_deny(tmp_path):
    engine()
    secret = tmp_path / "private"
    secret.mkdir()
    path = tmp_path / "requirements.toml"
    path.write_text('[permissions.filesystem]\ndeny_read=["./private"]')
    profile = {
        "type": "managed",
        "network": "restricted",
        "file_system": {
            "type": "restricted",
            "entries": [{"access": "read", "path": {"type": "path", "path": str(secret)}}],
        },
    }
    with pytest.raises(ValueError, match="readable root.*violates deny_read"):
        asyncio.run(
            run(tmp_path, profile, load_mcp_requirements(system_path=path), "print('unsafe')")
        )


@pytest.mark.parametrize("nested", [False, True])
def test_background_worker_retains_only_managed_denies_and_original_snapshot(
    tmp_path, monkeypatch, nested
):
    compiler = engine()
    managed_secret, user_secret = tmp_path / "managed.txt", tmp_path / "user.txt"
    managed_secret.write_text("managed-secret")
    user_secret.write_text("user-readable-by-worker")
    requirements_path = tmp_path / "requirements.toml"
    requirements_path.write_text('[permissions.filesystem]\ndeny_read=["./managed.txt"]')
    snapshot = load_mcp_requirements(system_path=requirements_path)
    profile = {
        "type": "managed",
        "file_system": {
            "type": "restricted",
            "entries": [
                {"access": "read", "path": {"type": "special", "value": {"kind": "root"}}},
                {"access": "write", "path": {"type": "path", "path": str(tmp_path)}},
                {"access": "deny", "path": {"type": "path", "path": str(user_secret)}},
            ],
        },
        "network": "restricted",
    }
    observed, requests = [], []

    def unexpected_reload():
        raise AssertionError("worker must inherit, not re-read later host requirements")

    monkeypatch.setattr("corki.core.runtime.load_mcp_requirements", unexpected_reload)
    script = f"""
from pathlib import Path
print(Path({str(user_secret)!r}).read_text())
try:
    print(Path({str(managed_secret)!r}).read_text())
except OSError:
    print('MANAGED_DENIED')
"""

    async def scenario():
        class Main:
            async def stream(self, request):
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class Memory:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    args = {"cmd": shlex.join([sys.executable, "-I", "-c", script]), "login": False}
                    call = (
                        ToolCall(new_tool_call_id(), "exec_command", args)
                        if not nested
                        else ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            raw_arguments="text(await tools.exec_command("
                            + json.dumps(args)
                            + "));",
                            input_kind="freeform",
                        )
                    )
                    item = ToolCallItem(call, turn, step)
                else:
                    observed.extend(
                        item.content for item in request.items if isinstance(item, ToolResultItem)
                    )
                    item = AssistantMessageItem(
                        json.dumps(
                            {"memory": "verified", "memory_summary": "verified", "skills": []}
                        ),
                        turn,
                        step,
                    )
                yield ModelCompleted((item,))

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                tool_mode="code_mode_only" if nested else "direct",
                memories_consolidation_model="fixture",
                execution_permissions=ExecutionPermissions(compiler, tmp_path, json.dumps(profile)),
            ),
            database_path=tmp_path / "sessions.db",
            memory_root=tmp_path / "memories",
            model=Main(),
            memory_model=Memory(),
            mcp_requirements=snapshot,
        )
        requirements_path.write_text("[permissions.filesystem]\ndeny_read=[]")
        try:
            events = [event async for event in runtime.stream("work")]
            assert isinstance(events[-1], TurnCompleted)
            report = await runtime._memory_service.wait()
            assert report.consolidated and not report.failed, runtime._memory_service.warnings
            assert len(requests) == 2
            assert any(
                "MANAGED_DENIED" in item and "user-readable-by-worker" in item for item in observed
            ), observed
            assert all("managed-secret" not in item for item in observed)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
