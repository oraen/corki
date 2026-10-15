"""Creation-time rules participate in the actual model/tool loop."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from corki.config import CorkiSettings
from corki.config.permissions import ExecutionPermissions
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted, WarningEvent
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall


@pytest.fixture
def compiler():
    value = os.environ.get("CORKI_TEST_SANDBOX_COMPILER")
    if not value or sys.platform != "darwin":
        pytest.skip("requires explicit native compiler and macOS")
    return Path(value)


class Model:
    def __init__(self, mode="direct", command="printf POLICY_FIXTURE"):
        self.mode, self.command, self.requests = mode, command, []

    async def stream(self, request):
        self.requests.append(request)
        turn, step = request.items[-1].turn_id, new_step_id()
        if self.command is not None and len(self.requests) % 2:
            arguments = {"cmd": self.command, "login": False}
            call = (
                ToolCall(new_tool_call_id(), "exec_command", arguments)
                if self.mode == "direct"
                else ToolCall(
                    new_tool_call_id(),
                    "exec",
                    None,
                    raw_arguments="text(await tools.exec_command(" + json.dumps(arguments) + "))",
                    input_kind="freeform",
                )
            )
            yield ModelCompleted((ToolCallItem(call, turn, step),))
        else:
            yield ModelCompleted((AssistantMessageItem("done", turn, step),))

    async def aclose(self):
        pass


async def runtime_for(root, compiler, model, **kwargs):
    return await LangGraphRuntime.acreate(
        settings=CorkiSettings(
            working_directory=root,
            skills_enabled=False,
            tool_mode=model.mode,
            execution_permissions=ExecutionPermissions(compiler, root, '{"type":"read-only"}'),
        ),
        database_path=root / "state.db",
        home_path=root / "home",
        model=model,
        **kwargs,
    )


async def run(runtime):
    events = [event async for event in runtime.stream("check rules")]
    assert isinstance(events[-1], TurnCompleted), events
    return events


def output(model):
    return [i for i in model.requests[-1].items if isinstance(i, ToolResultItem)][-1].content


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_home_rule_applies_without_explicit_sources(tmp_path, compiler, mode):
    async def scenario():
        rules = tmp_path / "home" / "rules"
        rules.mkdir(parents=True)
        (rules / "default.rules").write_text(
            'prefix_rule(pattern=["printf"], decision="forbidden", justification="HOME_RULE")'
        )
        model = Model(mode)
        runtime = await runtime_for(tmp_path, compiler, model)
        try:
            await run(runtime)
            assert "HOME_RULE" in output(model)
            assert not runtime._process_manager._sessions
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("with_command", [False, True])
def test_bad_rules_warn_once_and_discard_earlier_rules(tmp_path, compiler, with_command):
    async def scenario():
        rules = tmp_path / "home" / "rules"
        rules.mkdir(parents=True)
        (rules / "a.rules").write_text('prefix_rule(pattern=["printf"], decision="forbidden")')
        (rules / "b.rules").write_text("prefix_rule(")
        model = Model(command="printf POLICY_FIXTURE" if with_command else None)
        runtime = await runtime_for(tmp_path, compiler, model)
        try:
            events = await run(runtime)
            warnings = [e.message for e in events if isinstance(e, WarningEvent)]
            assert sum("b.rules" in warning for warning in warnings) == 1, warnings
            if with_command:
                assert "POLICY_FIXTURE" in output(model)
                assert "rejected" not in output(model)
            next_events = await run(runtime)
            assert not any(
                isinstance(e, WarningEvent) and "b.rules" in e.message for e in next_events
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["invalid_utf8", "not_directory"])
def test_rule_io_failure_prevents_sampling_and_thread_creation(tmp_path, compiler, failure):
    async def scenario():
        rules = tmp_path / "home" / "rules"
        rules.parent.mkdir()
        if failure == "not_directory":
            rules.write_text("not a directory")
        else:
            rules.mkdir()
            (rules / "invalid.rules").write_bytes(b"\xff")
        model = Model(command=None)
        runtime = await runtime_for(tmp_path, compiler, model)
        try:
            with pytest.raises(ValueError, match="rules"):
                await run(runtime)
            assert not model.requests
            assert await runtime._repository.latest_thread() is None
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_rules_snapshot_survives_turns_but_cold_runtime_reloads(tmp_path, compiler):
    async def scenario():
        rules = tmp_path / "home" / "rules"
        rules.mkdir(parents=True)
        source = rules / "default.rules"
        source.write_text('prefix_rule(pattern=["printf"], decision="forbidden")')
        model = Model()
        runtime = await runtime_for(tmp_path, compiler, model)
        try:
            await run(runtime)
            assert "rejected" in output(model)
            source.write_text("")
            await run(runtime)
            assert "rejected" in output(model)
            thread = runtime.thread_id
        finally:
            await runtime.aclose()
        model = Model()
        resumed = await runtime_for(tmp_path, compiler, model, thread_id=thread)
        try:
            await run(resumed)
            assert "POLICY_FIXTURE" in output(model)
            assert "rejected" not in output(model)
        finally:
            await resumed.aclose()

    asyncio.run(scenario())


def test_non_regular_and_non_rules_entries_are_not_loaded(tmp_path, compiler):
    async def scenario():
        rules = tmp_path / "home" / "rules"
        rules.mkdir(parents=True)
        (rules / "directory.rules").mkdir()
        (rules / "bad.txt").write_bytes(b"\xff")
        target = tmp_path / "external.rules"
        target.write_bytes(b"\xff")
        (rules / "link.rules").symlink_to(target)
        (rules / "missing.rules").symlink_to(tmp_path / "absent")
        model = Model()
        runtime = await runtime_for(tmp_path, compiler, model)
        try:
            events = await run(runtime)
            assert "POLICY_FIXTURE" in output(model)
            assert not any(isinstance(e, WarningEvent) and "rules" in e.message for e in events)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_sorted_earlier_parse_failure_precedes_later_read_failure(tmp_path, compiler):
    async def scenario():
        rules = tmp_path / "home" / "rules"
        rules.mkdir(parents=True)
        # Intentionally create the later filename first: directory iteration
        # order must not make an unreadable later source override parse fallback.
        (rules / "z.rules").write_bytes(b"\xff")
        (rules / "a.rules").write_text("prefix_rule(")
        model = Model(command=None)
        runtime = await runtime_for(tmp_path, compiler, model)
        try:
            events = await run(runtime)
            assert any(isinstance(e, WarningEvent) and "a.rules" in e.message for e in events)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_failed_initialization_does_not_retain_provisional_rules(tmp_path, compiler, monkeypatch):
    async def scenario():
        rules = tmp_path / "home" / "rules"
        rules.mkdir(parents=True)
        source = rules / "default.rules"
        source.write_text('prefix_rule(pattern=["printf"], decision="forbidden")')
        model = Model()
        runtime = await runtime_for(tmp_path, compiler, model)
        start = runtime._mcp_manager.start_session

        async def fail_start():
            raise OSError("fixture startup failure after rules resolution")

        monkeypatch.setattr(runtime._mcp_manager, "start_session", fail_start)
        try:
            with pytest.raises(OSError, match="fixture startup failure"):
                await run(runtime)
            assert not model.requests
            source.write_text("")
            monkeypatch.setattr(runtime._mcp_manager, "start_session", start)
            await run(runtime)
            assert "POLICY_FIXTURE" in output(model)
            assert "rejected" not in output(model)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("changed_home", [False, True])
@pytest.mark.parametrize("inherit", [False, True])
def test_child_reuses_snapshot_only_for_same_config_folders(
    tmp_path, compiler, changed_home, inherit
):
    from corki.protocol.session_source import SessionSource, SubAgentSource, ThreadSpawnSource

    async def scenario():
        rules = tmp_path / "home" / "rules"
        rules.mkdir(parents=True)
        source = rules / "default.rules"
        source.write_text('prefix_rule(pattern=["printf"], decision="forbidden")')
        parent = await runtime_for(tmp_path, compiler, Model(command=None))
        try:
            await run(parent)
            settings = parent._settings
            source.write_text("")
            model = Model()
            child = await LangGraphRuntime.acreate(
                settings=settings,
                database_path=tmp_path / "child.db",
                home_path=tmp_path / ("different-home" if changed_home else "home"),
                session_source=(
                    SessionSource.subagent(
                        SubAgentSource("thread_spawn", ThreadSpawnSource(parent.thread_id, 1))
                    )
                    if inherit
                    else SessionSource.internal("memory_consolidation")
                ),
                inherited_exec_policy=(
                    settings.execution_permissions.exec_policy_snapshot if inherit else None
                ),
                model=model,
            )
            try:
                await run(child)
                assert ("rejected" in output(model)) == (inherit and not changed_home)
            finally:
                await child.aclose()
        finally:
            await parent.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("guardian", [False, True])
def test_only_typed_basic_guardian_omits_home_rules(tmp_path, compiler, guardian):
    from corki.protocol.session_source import SessionSource

    async def scenario():
        rules = tmp_path / "home" / "rules"
        rules.mkdir(parents=True)
        (rules / "default.rules").write_text(
            'prefix_rule(pattern=["printf"], decision="forbidden")'
        )
        model = Model()
        runtime = await runtime_for(
            tmp_path,
            compiler,
            model,
            session_source=(
                SessionSource.internal("guardian")
                if guardian
                else SessionSource.from_startup_arg("guardian")
            ),
        )
        try:
            await run(runtime)
            assert ("rejected" in output(model)) is not guardian
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
