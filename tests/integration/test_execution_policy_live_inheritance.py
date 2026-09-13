"""Live host inheritance shares policy updates, never session consent or owners."""

import asyncio
import json
import os
import shlex
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from corki.code_mode.service import CodeModeService
from corki.config import CorkiSettings
from corki.config.exec_policy import ExecPolicySource
from corki.config.execution_requirements import ExecutionRequirementsLayer
from corki.config.permissions import ExecutionPermissions
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted, WarningEvent
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import ContextItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.session_source import SessionSource, SubAgentSource, ThreadSpawnSource
from corki.protocol.tools import ToolCall


@pytest.fixture
def compiler():
    value = os.environ.get("CORKI_TEST_SANDBOX_COMPILER")
    if not value or sys.platform != "darwin":
        pytest.skip("requires explicit native compiler and macOS")
    return Path(value)


class Model:
    def __init__(self, mode):
        self.mode, self.requests, self.command = mode, [], None

    async def stream(self, request):
        self.requests.append(request)
        command, self.command = self.command, None
        if command is None:
            yield ModelCompleted(())
            return
        args = {"login": False, **command}
        call = (
            ToolCall(new_tool_call_id(), "exec_command", args)
            if self.mode == "direct"
            else ToolCall(
                new_tool_call_id(),
                "exec",
                None,
                input_kind="freeform",
                raw_arguments="text(await tools.exec_command(" + json.dumps(args) + "))",
            )
        )
        yield ModelCompleted((ToolCallItem(call, request.items[-1].turn_id, new_step_id()),))

    async def aclose(self):
        pass


def runtime_for(root, compiler, model, *, name="parent", settings=None, **kwargs):
    if model.mode != "direct" and not CodeModeService.available():
        pytest.skip("install corki[code-mode]")
    home = kwargs.pop("home_path", root / "home")
    home.mkdir(exist_ok=True)
    return LangGraphRuntime.create(
        settings=settings
        or CorkiSettings(
            working_directory=root,
            skills_enabled=False,
            model="fixture",
            model_contexts=(ModelContextInfo(model="fixture"),),
            tool_mode=model.mode,
            execution_permissions=ExecutionPermissions(
                compiler, root, '{"type":"read-only"}', approval_policy_json='"on-request"'
            ),
        ),
        database_path=root / f"{name}.db",
        home_path=home,
        model=model,
        **kwargs,
    )


def live_policy(runtime):
    return runtime.execution_policy_handle


def child_source(parent):
    return SessionSource.subagent(
        SubAgentSource("thread_spawn", ThreadSpawnSource(parent.thread_id, 1))
    )


async def run(runtime, model, command=None):
    model.command = command
    events = [event async for event in runtime.stream("exercise host policy inheritance")]
    assert isinstance(events[-1], TurnCompleted), events
    return events


def notices(model):
    return [
        item.content
        for item in model.requests[-1].items
        if isinstance(item, ContextItem)
        and item.content_kind == "permissions.approved_command_prefix_saved"
    ]


def approve(runtime, scope):
    prompts = []

    async def respond(request):
        prompts.append(request)
        runtime.respond_execution_approval(
            request.request_id,
            "accept",
            remember=scope == "session",
            execpolicy_amendment=["touch"] if scope == "rule" else None,
        )

    runtime.set_execution_approval_handler(respond)
    return prompts


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("scope", ["once", "session", "rule"])
@pytest.mark.parametrize("publisher", ["parent", "child"])
@pytest.mark.parametrize("close_publisher", [False, True])
def test_initialized_relatives_share_saved_rules_but_not_consent(
    tmp_path, compiler, mode, scope, publisher, close_publisher
):
    async def scenario():
        parent_model, child_model = Model(mode), Model(mode)
        parent = runtime_for(tmp_path, compiler, parent_model)
        child = None
        try:
            await run(parent, parent_model)
            child = runtime_for(
                tmp_path,
                compiler,
                child_model,
                name="child",
                settings=parent._settings,
                session_source=child_source(parent),
                inherited_exec_policy=live_policy(parent),
            )
            await run(child, child_model)
            owner, owner_model, consumer, consumer_model = (
                (parent, parent_model, child, child_model)
                if publisher == "parent"
                else (child, child_model, parent, parent_model)
            )
            prompts = approve(owner, scope)
            approved, subsequent = tmp_path / "approved", tmp_path / "subsequent"
            owner_events = await run(
                owner,
                owner_model,
                {
                    "cmd": "touch " + shlex.quote(str(approved)),
                    "sandbox_permissions": "require_escalated",
                    "prefix_rule": ["touch"],
                },
            )
            assert approved.exists() and len(prompts) == 1
            assert not [event.message for event in owner_events if isinstance(event, WarningEvent)]
            if scope == "rule":
                assert owner._process_manager.approvals.rules.prefixes == (("touch",),)
                assert 'decision="allow"' in (tmp_path / "home/rules/default.rules").read_text()
            if close_publisher:
                await owner.aclose()
            await run(consumer, consumer_model, {"cmd": "touch " + shlex.quote(str(subsequent))})
            assert subsequent.exists() is (scope == "rule"), (
                consumer._process_manager.approvals.rules.prefixes,
                [
                    item.content
                    for item in consumer_model.requests[-1].items
                    if isinstance(item, ToolResultItem)
                ],
            )
            assert bool(notices(consumer_model)) is (scope == "rule")
            assert not consumer._process_manager.approvals._session
            assert not consumer._process_manager.approvals.router._pending
            assert (
                consumer._process_manager.approvals.router
                is not owner._process_manager.approvals.router
            )
        finally:
            if child is not None:
                await child.aclose()
            await parent.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("closing", ["parent", "child"])
def test_closing_relative_does_not_cancel_another_sessions_pending_approval(
    tmp_path, compiler, mode, closing
):
    async def scenario():
        parent_model, child_model = Model(mode), Model(mode)
        parent = runtime_for(tmp_path, compiler, parent_model)
        child, task = None, None
        entered, release = asyncio.Event(), asyncio.Event()
        try:
            await run(parent, parent_model)
            child = runtime_for(
                tmp_path,
                compiler,
                child_model,
                name="child",
                settings=parent._settings,
                session_source=child_source(parent),
                inherited_exec_policy=live_policy(parent),
            )
            await run(child, child_model)
            owner, model, relative = (
                (parent, parent_model, child)
                if closing == "child"
                else (child, child_model, parent)
            )

            async def respond(request):
                entered.set()
                await release.wait()
                owner.respond_execution_approval(
                    request.request_id, "accept", execpolicy_amendment=["touch"]
                )

            owner.set_execution_approval_handler(respond)
            target = tmp_path / "approval-survived"
            task = asyncio.create_task(
                run(
                    owner,
                    model,
                    {
                        "cmd": "touch " + shlex.quote(str(target)),
                        "sandbox_permissions": "require_escalated",
                        "prefix_rule": ["touch"],
                    },
                )
            )
            await asyncio.wait_for(entered.wait(), 5)
            await relative.aclose()
            assert not task.done() and not target.exists()
            assert owner._process_manager.approvals.router._pending
            release.set()
            await asyncio.wait_for(task, 5)
            assert target.exists()
            assert owner._process_manager.approvals.rules.prefixes == (("touch",),)
            assert not owner._process_manager.approvals.router._pending
        finally:
            release.set()
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if child is not None:
                await child.aclose()
            await parent.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("variant", ["review", "compact"])
def test_explicit_delegate_handle_survives_bad_user_rule_fallback(
    tmp_path, compiler, mode, variant
):
    async def scenario():
        rules = tmp_path / "home/rules"
        rules.mkdir(parents=True)
        (rules / "bad.rules").write_text("not valid rules !")
        parent_model, child_model = Model(mode), Model(mode)
        parent = runtime_for(tmp_path, compiler, parent_model)
        child = None
        try:
            events = await run(parent, parent_model)
            assert any(
                isinstance(e, WarningEvent) and "failed to parse" in e.message for e in events
            )
            child = runtime_for(
                tmp_path,
                compiler,
                child_model,
                name="delegate",
                settings=parent._settings,
                session_source=SessionSource.subagent(SubAgentSource(variant)),
                inherited_exec_policy=live_policy(parent),
            )
            await run(child, child_model)
            approve(parent, "rule")
            await run(
                parent,
                parent_model,
                {
                    "cmd": "touch " + shlex.quote(str(tmp_path / "approved")),
                    "sandbox_permissions": "require_escalated",
                    "prefix_rule": ["touch"],
                },
            )
            target = tmp_path / "delegate-allowed"
            await run(child, child_model, {"cmd": "touch " + shlex.quote(str(target))})
            assert target.exists() and notices(child_model)
        finally:
            if child is not None:
                await child.aclose()
            await parent.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_failed_save_does_not_publish_to_initialized_child(tmp_path, compiler, mode):
    async def scenario():
        parent_model, child_model = Model(mode), Model(mode)
        parent = runtime_for(tmp_path, compiler, parent_model)
        child = None
        try:
            await run(parent, parent_model)
            child = runtime_for(
                tmp_path,
                compiler,
                child_model,
                name="child",
                settings=parent._settings,
                session_source=child_source(parent),
                inherited_exec_policy=live_policy(parent),
            )
            await run(child, child_model)
            (tmp_path / "home/rules/default.rules").mkdir(parents=True)
            approve(parent, "rule")
            approved = tmp_path / "current-approval"
            events = await run(
                parent,
                parent_model,
                {
                    "cmd": "touch " + shlex.quote(str(approved)),
                    "sandbox_permissions": "require_escalated",
                    "prefix_rule": ["touch"],
                },
            )
            assert approved.exists()
            assert any(
                isinstance(e, WarningEvent) and "Failed to apply" in e.message for e in events
            )
            target = tmp_path / "no-grant"
            await run(child, child_model, {"cmd": "touch " + shlex.quote(str(target))})
            assert not target.exists() and not notices(child_model)
            assert not parent._process_manager.approvals.rules.prefixes
            assert not child._process_manager.approvals.rules.prefixes
        finally:
            if child is not None:
                await child.aclose()
            await parent.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_cross_event_loop_live_inheritance_rejected_before_sampling(tmp_path, compiler, mode):
    async def capture():
        model = Model(mode)
        parent = runtime_for(tmp_path, compiler, model)
        try:
            with pytest.raises(RuntimeError, match="ready"):
                live_policy(parent)
            await run(parent, model)
            return live_policy(parent), parent._settings, child_source(parent)
        finally:
            await parent.aclose()

    handle, settings, source = asyncio.run(capture())

    async def scenario():
        model = Model(mode)
        child = runtime_for(
            tmp_path,
            compiler,
            model,
            name="child",
            settings=settings,
            session_source=source,
            inherited_exec_policy=handle,
        )
        try:
            with pytest.raises(RuntimeError, match="owning event loop"):
                await run(child, model)
            assert not model.requests
            assert await child._repository.latest_thread() is None
        finally:
            await child.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_concurrent_relatives_preserve_both_rule_updates(tmp_path, compiler, mode):
    async def scenario():
        parent_model, child_model = Model(mode), Model(mode)
        parent = runtime_for(tmp_path, compiler, parent_model)
        child = None
        try:
            await run(parent, parent_model)
            child = runtime_for(
                tmp_path,
                compiler,
                child_model,
                name="child",
                settings=parent._settings,
                session_source=child_source(parent),
                inherited_exec_policy=live_policy(parent),
            )
            await run(child, child_model)

            def handler(runtime):
                async def respond(request):
                    runtime.respond_execution_approval(
                        request.request_id,
                        "accept",
                        execpolicy_amendment=request.params["_meta"]["execpolicy_amendment"],
                    )

                return respond

            parent.set_execution_approval_handler(handler(parent))
            child.set_execution_approval_handler(handler(child))
            file, directory = tmp_path / "file", tmp_path / "directory"
            await asyncio.gather(
                run(
                    parent,
                    parent_model,
                    {
                        "cmd": "touch " + shlex.quote(str(file)),
                        "sandbox_permissions": "require_escalated",
                        "prefix_rule": ["touch"],
                    },
                ),
                run(
                    child,
                    child_model,
                    {
                        "cmd": "mkdir " + shlex.quote(str(directory)),
                        "sandbox_permissions": "require_escalated",
                        "prefix_rule": ["mkdir"],
                    },
                ),
            )
            assert file.is_file() and directory.is_dir()
            assert set(parent._process_manager.approvals.rules.prefixes) == {("touch",), ("mkdir",)}
            assert (
                child._process_manager.approvals.rules.prefixes
                == parent._process_manager.approvals.rules.prefixes
            )
            saved = (tmp_path / "home/rules/default.rules").read_text().splitlines()
            assert len(saved) == 2 and all('decision="allow"' in line for line in saved)
            child_target, parent_target = tmp_path / "other-file", tmp_path / "other-directory"
            await asyncio.gather(
                run(child, child_model, {"cmd": "touch " + shlex.quote(str(child_target))}),
                run(parent, parent_model, {"cmd": "mkdir " + shlex.quote(str(parent_target))}),
            )
            assert child_target.is_file() and parent_target.is_dir()
        finally:
            if child is not None:
                await child.aclose()
            await parent.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("invalid", ["root", "custom-lookalike", "unconfigured"])
def test_live_handle_cannot_bypass_non_root_configured_host_boundary(tmp_path, compiler, invalid):
    async def scenario():
        model = Model("direct")
        parent = runtime_for(tmp_path, compiler, model)
        try:
            await run(parent, model)
            settings = parent._settings
            if invalid == "unconfigured":
                settings = replace(settings, execution_permissions=None)
            with pytest.raises(ValueError, match="configured non-root host"):
                runtime_for(
                    tmp_path,
                    compiler,
                    Model("direct"),
                    name="invalid",
                    settings=settings,
                    session_source=(
                        child_source(parent)
                        if invalid == "unconfigured"
                        else SessionSource.from_startup_arg("thread_spawn")
                        if invalid == "custom-lookalike"
                        else SessionSource()
                    ),
                    inherited_exec_policy=live_policy(parent),
                )
            assert not (tmp_path / "invalid.db").exists()
        finally:
            await parent.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize(
    "isolation", ["home", "managed", "declared", "guardian", "memory", "snapshot"]
)
def test_incompatible_or_independent_children_do_not_receive_live_updates(
    tmp_path, compiler, mode, isolation
):
    async def scenario():
        parent_model, child_model = Model(mode), Model(mode)
        parent = runtime_for(tmp_path, compiler, parent_model)
        child = None
        try:
            await run(parent, parent_model)
            settings = parent._settings
            if isolation == "managed":
                settings = replace(
                    settings,
                    execution_permissions=replace(
                        settings.execution_permissions,
                        requirements=(
                            ExecutionRequirementsLayer(
                                "different-managed",
                                json.dumps(
                                    {
                                        "rules": {
                                            "prefix_rules": [
                                                {
                                                    "pattern": [{"token": "printf"}],
                                                    "decision": "forbidden",
                                                }
                                            ]
                                        }
                                    }
                                ),
                            ),
                        ),
                    ),
                )
            if isolation == "declared":
                settings = replace(
                    settings,
                    execution_permissions=replace(
                        settings.execution_permissions,
                        exec_policy_sources=(
                            ExecPolicySource(str(tmp_path / "declared.rules"), ""),
                        ),
                    ),
                )
            child = runtime_for(
                tmp_path,
                compiler,
                child_model,
                name="child",
                settings=settings,
                home_path=tmp_path / ("other-home" if isolation == "home" else "home"),
                session_source=(
                    SessionSource.internal(
                        "guardian" if isolation == "guardian" else "memory_consolidation"
                    )
                    if isolation in {"guardian", "memory"}
                    else child_source(parent)
                ),
                inherited_exec_policy=(
                    None
                    if isolation == "memory"
                    else parent._settings.execution_permissions.exec_policy_snapshot
                    if isolation == "snapshot"
                    else live_policy(parent)
                ),
            )
            await run(child, child_model)
            approve(parent, "rule")
            await run(
                parent,
                parent_model,
                {
                    "cmd": "touch " + shlex.quote(str(tmp_path / "approved")),
                    "sandbox_permissions": "require_escalated",
                    "prefix_rule": ["touch"],
                },
            )
            target = tmp_path / "must-stay-sandboxed"
            await run(child, child_model, {"cmd": "touch " + shlex.quote(str(target))})
            assert not target.exists()
            assert not notices(child_model)
        finally:
            if child is not None:
                await child.aclose()
            await parent.aclose()

    asyncio.run(scenario())
