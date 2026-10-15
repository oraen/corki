import asyncio
import json
import os
import shlex
import sys

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall


def settings(tmp_path, configuration):
    compiler = os.environ.get("CORKI_TEST_SANDBOX_COMPILER")
    if not compiler or sys.platform != "darwin":
        pytest.skip("requires built sandbox compiler and macOS")
    path = tmp_path / "config.toml"
    path.write_text(configuration + "\n[execution]\ncompiler=" + json.dumps(compiler))
    return CorkiSettings.for_directory(tmp_path, config_file=path)


async def run(tmp_path, configuration, *, nested=False, requirements=None):
    from dataclasses import replace

    chosen = replace(
        settings(tmp_path, configuration),
        skills_enabled=False,
        tool_mode="code_mode_only" if nested else "direct",
    )
    requests = []
    script = """
from pathlib import Path
for name in ['allowed/inside.txt', 'outside.txt', 'extra/allowed/inside.txt']:
    try:
        Path(name).write_text('created')
        print(name + '=written')
    except OSError:
        print(name + '=denied')
"""

    class Model:
        async def stream(self, request):
            requests.append(request)
            turn, step = request.items[-1].turn_id, new_step_id()
            if len(requests) % 2:
                args = {"cmd": shlex.join([sys.executable, "-I", "-c", script]), "login": False}
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
        settings=chosen,
        model=Model(),
        database_path=tmp_path / "session.db",
        mcp_requirements=requirements,
    )
    try:
        events = [event async for event in runtime.stream("exercise profile")]
        assert isinstance(events[-1], TurnCompleted)
        permissions = runtime._graph._settings.execution_permissions
        await runtime.update_thread_settings(reasoning_effort="high")
        events = [event async for event in runtime.stream("exercise next turn")]
        assert isinstance(events[-1], TurnCompleted)
        assert runtime._graph._settings.execution_permissions == permissions
        return permissions, [
            item for item in requests[-1].items if isinstance(item, ToolResultItem)
        ]
    except ValueError:
        assert not requests, "invalid selection must fail before model sampling"
        raise
    finally:
        await runtime.aclose()


@pytest.mark.parametrize("nested", [False, True])
def test_named_inheritance_compiles_real_permissions_and_identity(tmp_path, nested):
    (tmp_path / "allowed").mkdir()
    configuration = """
default_permissions="build"
[permissions.base]
extends=":read-only"
[permissions.base.filesystem.":workspace_roots"]
allowed="write"
[permissions.build]
extends="base"
description="selected build"
"""
    permissions, results = asyncio.run(run(tmp_path, configuration, nested=nested))
    assert (tmp_path / "allowed/inside.txt").read_text() == "created"
    assert not (tmp_path / "outside.txt").exists()
    assert any("outside.txt=denied" in item.content for item in results)
    assert permissions.active_profile.id == "build"
    assert permissions.active_profile.extends == "base"


def test_implicit_builtin_unknown_trust_is_read_only(tmp_path):
    (tmp_path / "allowed").mkdir()
    permissions, results = asyncio.run(run(tmp_path, ""))
    assert not (tmp_path / "allowed/inside.txt").exists()
    assert not (tmp_path / "outside.txt").exists()
    assert permissions.active_profile.id == ":read-only"
    assert any("outside.txt=denied" in item.content for item in results)


def test_empty_declared_profile_name_matches_native(tmp_path):
    (tmp_path / "allowed").mkdir()
    permissions, _ = asyncio.run(
        run(tmp_path, 'default_permissions=""\n[permissions.""]\nextends=":read-only"')
    )
    assert permissions.active_profile.id == ""
    assert not (tmp_path / "outside.txt").exists()


def test_named_workspace_roots_materialize_against_policy_cwd(tmp_path):
    from pathlib import Path

    (tmp_path / "allowed").mkdir()
    (tmp_path / "extra/allowed").mkdir(parents=True)
    configuration = """
default_permissions="build"
[permissions.build]
extends=":read-only"
[permissions.build.workspace_roots]
extra=true
disabled=false
[permissions.build.filesystem.":workspace_roots"]
allowed="write"
"""
    permissions, _ = asyncio.run(run(tmp_path, configuration))
    assert (tmp_path / "extra/allowed/inside.txt").read_text() == "created"
    assert permissions.profile_workspace_roots == (Path(tmp_path / "extra"),)
    assert not (tmp_path / "outside.txt").exists()


def test_managed_fallback_clears_named_identity_and_roots(tmp_path):
    from corki.config.managed_mcp import MCPRequirementsLayer, compose_mcp_requirements

    (tmp_path / "allowed").mkdir()
    requirements = compose_mcp_requirements(
        (MCPRequirementsLayer("host", 'allowed_sandbox_modes=["read-only"]'),)
    )
    configuration = """
default_permissions="build"
[permissions.build]
extends=":workspace"
[permissions.build.workspace_roots]
extra=true
"""
    permissions, _ = asyncio.run(run(tmp_path, configuration, requirements=requirements))
    assert permissions.active_profile is None
    assert permissions.profile_workspace_roots == ()
    assert not (tmp_path / "allowed/inside.txt").exists()


def test_unknown_special_path_warns_without_dropping_identity(tmp_path):
    (tmp_path / "allowed").mkdir()
    configuration = """
default_permissions="future"
[permissions.future]
extends=":read-only"
[permissions.future.filesystem]
":future-permission"="write"
"""
    permissions, _ = asyncio.run(run(tmp_path, configuration))
    assert permissions.active_profile.id == "future"
    assert not (tmp_path / "outside.txt").exists()


@pytest.mark.parametrize(
    ("configuration", "message"),
    [
        ('default_permissions="missing"', "requires a.*permissions.*table"),
        ('[permissions.unselected]\nextends=":read-only"', "does not set default_permissions"),
        ('default_permissions=":unknown"', "unknown built-in profile"),
        (
            'default_permissions="a"\n[permissions.a]\nextends="b"\n[permissions.b]\nextends="a"',
            "cycle",
        ),
        ('default_permissions="a"\n[permissions.a]\nextends="missing"', "undefined profile"),
        (
            'default_permissions="a"\n[permissions.a]\nextends=":danger-full-access"',
            "unsupported built-in",
        ),
        ('default_permissions=":read-only"\n[permissions.":reserved"]', "reserved built-in"),
        (
            'default_permissions="a"\n[permissions.a.filesystem.":workspace_roots"]\n"../escape"="write"',
            "descendant path",
        ),
        (
            'default_permissions="a"\n[permissions.a.network]\nproxy_url="http://localhost:9999"',
            "not yet supported",
        ),
    ],
)
def test_invalid_named_selection_fails_before_model(tmp_path, configuration, message):
    with pytest.raises(ValueError, match=message):
        asyncio.run(run(tmp_path, configuration))


def test_memory_derivation_clears_parent_name_and_profile_roots(tmp_path):
    from corki.execution.backend import derive_memory_permissions, resolve_execution_permissions

    configuration = """
default_permissions="build"
[permissions.build]
extends=":read-only"
[permissions.build.workspace_roots]
extra=true
"""
    configured = settings(tmp_path, configuration).execution_permissions
    root = tmp_path / "memory-worker"
    root.mkdir()

    async def scenario():
        parent, _ = await resolve_execution_permissions(configured)
        assert parent.active_profile.id == "build"
        assert parent.profile_workspace_roots
        child = await derive_memory_permissions(parent, root)
        assert child.active_profile is None
        assert child.profile_workspace_roots == ()
        assert child.policy_cwd == root
        assert json.loads(child.profile_json)["type"] == "managed"

    asyncio.run(scenario())


def test_cancel_during_selection_does_not_publish_or_start_runtime(tmp_path, monkeypatch):
    configured = settings(tmp_path, 'default_permissions=":read-only"')

    async def scenario():
        entered, cleaned = asyncio.Event(), asyncio.Event()

        async def held(permissions, *, exec_policy_config_folders):
            assert exec_policy_config_folders
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned.set()

        monkeypatch.setattr("corki.core.runtime.resolve_execution_permissions", held)

        class Model:
            async def stream(self, request):
                raise AssertionError("selection cancelled before sampling")
                yield

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=configured, model=Model(), database_path=tmp_path / "sessions.db"
        )

        async def consume():
            return [event async for event in runtime.stream("cancel me")]

        task = asyncio.create_task(consume())
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await task
            assert cleaned.is_set()
            assert not runtime._thread_created
            assert runtime._graph._settings.execution_permissions.needs_resolution
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_startup_retry_preserves_resolved_name_and_roots(tmp_path, monkeypatch):
    from corki.config.managed_mcp import MCPRequirementsLayer, compose_mcp_requirements
    from corki.mcp import MCPManager

    chosen = settings(
        tmp_path,
        """
default_permissions="build"
[permissions.build]
extends=":read-only"
[permissions.build.workspace_roots]
extra=true
""",
    )
    constraints = compose_mcp_requirements(
        (MCPRequirementsLayer("host", 'allowed_sandbox_modes=["read-only","workspace-write"]'),)
    )
    start = MCPManager.start_session
    attempts = []

    async def first_fails(manager):
        attempts.append(manager)
        if len(attempts) == 1:
            raise ValueError("temporary startup failure")
        await start(manager)

    monkeypatch.setattr(MCPManager, "start_session", first_fails)

    async def scenario():
        class Model:
            async def stream(self, request):
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=chosen,
            model=Model(),
            database_path=tmp_path / "sessions.db",
            mcp_requirements=constraints,
        )
        try:
            with pytest.raises(ValueError, match="temporary startup failure"):
                _ = [event async for event in runtime.stream("first")]
            first = runtime._graph._settings.execution_permissions
            assert first.active_profile.id == "build"
            assert first.profile_workspace_roots
            events = [event async for event in runtime.stream("retry")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(attempts) == 2
            assert runtime._graph._settings.execution_permissions == first
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
