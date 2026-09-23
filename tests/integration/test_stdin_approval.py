"""Retained terminal authority requires fresh, once-only input review when enabled."""

import asyncio
import json
import tomllib
from dataclasses import replace

import pytest
from test_execution_approval_cancel import observe
from test_execution_policy_live_inheritance import compiler as compiler
from test_execution_policy_live_inheritance import runtime_for
from test_sandbox_denial_retry import capture_spawns

from corki.config import CorkiSettings
from corki.config.execution_requirements import ExecutionRequirementsLayer
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.config.permissions import ExecutionPermissions
from corki.models import ModelCompleted
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCancelled, TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall


class Model:
    def __init__(self, mode):
        self.mode, self.pending, self.requests = mode, None, []

    async def stream(self, request):
        self.requests.append(request)
        pending, self.pending = self.pending, None
        if pending is None:
            yield ModelCompleted(())
            return
        name, args = pending
        call = (
            ToolCall(new_tool_call_id(), name, args)
            if self.mode == "direct"
            else ToolCall(
                new_tool_call_id(),
                "exec",
                None,
                input_kind="freeform",
                raw_arguments=f"text(await tools.{name}({json.dumps(args)}))",
            )
        )
        yield ModelCompleted((ToolCallItem(call, request.items[-1].turn_id, new_step_id()),))

    async def aclose(self):
        pass


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_old_compiler_cannot_enable_review_silently(tmp_path, compiler, mode, monkeypatch):
    from corki.execution import backend

    original = backend.run_owned
    launches = capture_spawns(monkeypatch)

    async def old_compiler(*args, **kwargs):
        response = json.loads(await original(*args, **kwargs))
        if "ok" in response:
            response["ok"].pop("terminal_review_supported", None)
            response["ok"].pop("terminal_snapshot", None)
        return json.dumps(response).encode()

    monkeypatch.setattr(backend, "run_owned", old_compiler)

    async def scenario():
        model = Model(mode)
        runtime = await runtime_for(
            tmp_path, compiler, model, settings=settings_for(tmp_path, compiler, mode, monkeypatch)
        )
        try:
            result = await call(
                runtime,
                model,
                "exec_command",
                {"cmd": "echo never", "tty": True, "login": False, "yield_time_ms": 250},
            )
            assert "does not support terminal review" in result.content
            assert not launches and not runtime._process_manager._starting
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


async def call(runtime, model, name, args):
    model.pending = name, args
    events = [event async for event in runtime.stream("exercise terminal review")]
    assert isinstance(events[-1], TurnCompleted), events
    return [item for item in model.requests[-1].items if isinstance(item, ToolResultItem)][-1]


def settings_for(root, compiler, mode, monkeypatch, enabled=True):
    configuration = LocalConfigState(
        layers=(
            ConfigLayer(
                root / "fixture-config.toml",
                "user",
                contents=f"[features]\nwrite_stdin_approval = {str(enabled).lower()}\n",
            ),
        )
    )
    monkeypatch.setattr(
        "corki.config.settings.load_local_config",
        lambda *args: ({"features": {"write_stdin_approval": enabled}}, configuration),
    )
    return replace(
        CorkiSettings.for_directory(root),
        skills_enabled=False,
        model="fixture",
        model_contexts=(ModelContextInfo(model="fixture"),),
        tool_mode=mode,
        execution_permissions=ExecutionPermissions(
            compiler, root, '{"type":"read-only"}', approval_policy_json='"on-request"'
        ),
    )


@pytest.mark.parametrize("enabled", [False, True])
def test_review_fixture_has_consistent_configuration(tmp_path, monkeypatch, enabled):
    # Keep the fixture contract covered even when native execution tests skip.
    settings = settings_for(tmp_path, tmp_path / "unused-compiler", "direct", monkeypatch, enabled)
    assert settings.write_stdin_approval is enabled
    (layer,) = settings.configuration.layers
    assert layer.kind == "user"
    assert tomllib.loads(layer.contents)["features"]["write_stdin_approval"] is enabled


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("decision", ["accept", "decline"])
def test_escalated_terminal_input_has_fresh_review(tmp_path, compiler, mode, decision, monkeypatch):
    async def scenario():
        model = Model(mode)
        runtime = await runtime_for(
            tmp_path, compiler, model, settings=settings_for(tmp_path, compiler, mode, monkeypatch)
        )
        target = tmp_path / "received"
        prompts = []

        async def approve(request):
            prompts.append(request)
            assert not target.exists()
            runtime.respond_execution_approval(
                request.request_id,
                "accept" if len(prompts) == 1 else decision,
                remember=len(prompts) == 1,
            )

        runtime.set_execution_approval_handler(approve)
        try:
            await call(
                runtime,
                model,
                "exec_command",
                {
                    "cmd": "read line; printf '%s' \"$line\" > received",
                    "tty": True,
                    "login": False,
                    "yield_time_ms": 250,
                    "sandbox_permissions": "require_escalated",
                },
            )
            session_id = next(iter(runtime._process_manager._sessions))
            await call(
                runtime,
                model,
                "write_stdin",
                {"session_id": session_id, "chars": "hello\n", "yield_time_ms": 250},
            )
            assert len(prompts) == 2
            assert target.exists() is (decision == "accept")
            if decision == "accept":
                assert target.read_text() == "hello"
            else:
                assert session_id in runtime._process_manager._sessions
            assert not runtime._process_manager.approvals.router._pending
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("policy", ["never", "granular-disabled", "deny-read", "on-request"])
def test_current_policy_controls_retained_authority(tmp_path, compiler, monkeypatch, policy):
    async def scenario():
        model = Model("direct")
        runtime = await runtime_for(
            tmp_path,
            compiler,
            model,
            settings=settings_for(tmp_path, compiler, "direct", monkeypatch),
        )
        prompts = []

        async def approve(request):
            prompts.append(request)
            runtime.respond_execution_approval(request.request_id, "accept")

        runtime.set_execution_approval_handler(approve)
        manager = runtime._process_manager
        try:
            await call(
                runtime,
                model,
                "exec_command",
                {
                    "cmd": "read line; printf '%s' \"$line\" > received",
                    "tty": True,
                    "login": False,
                    "yield_time_ms": 250,
                    "sandbox_permissions": "require_escalated",
                },
            )
            session = next(iter(manager._sessions.values()))
            current = runtime._settings.execution_permissions
            if policy == "never":
                current = replace(current, approval_policy_json='"never"')
            elif policy == "granular-disabled":
                current = replace(
                    current,
                    approval_policy_json=json.dumps(
                        {
                            "granular": {
                                "sandbox_approval": False,
                                "rules": True,
                                "mcp_elicitations": False,
                            }
                        }
                    ),
                )
            elif policy == "deny-read":
                current = replace(
                    current,
                    requirements=(
                        ExecutionRequirementsLayer(
                            "organization",
                            json.dumps(
                                {
                                    "permissions": {
                                        "filesystem": {"deny_read": [str(tmp_path / "secret")]}
                                    }
                                }
                            ),
                        ),
                    ),
                )
            if policy == "on-request":
                await manager.write_stdin(
                    session.id,
                    "safe\n",
                    yield_seconds=0.25,
                    permissions=current,
                    policy_cwd=tmp_path,
                    call_id="new-input",
                    review_enabled=True,
                )
                assert len(prompts) == 2
                assert (tmp_path / "received").read_text() == "safe"
            else:
                with pytest.raises(ValueError, match="approval|denied-read"):
                    await manager.write_stdin(
                        session.id,
                        "unsafe\n",
                        yield_seconds=0.25,
                        permissions=current,
                        policy_cwd=tmp_path,
                        call_id="new-input",
                        review_enabled=True,
                    )
                assert not (tmp_path / "received").exists()
                assert len(prompts) == 1
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("action", ["cancel", "close", "replace", "remember", "oversized", "nul"])
def test_review_never_delivers_unapproved_input(tmp_path, compiler, mode, action, monkeypatch):
    async def scenario():
        model = Model(mode)
        runtime = await runtime_for(
            tmp_path, compiler, model, settings=settings_for(tmp_path, compiler, mode, monkeypatch)
        )
        manager = runtime._process_manager
        target = tmp_path / "received"
        entered, cleaned = asyncio.Event(), asyncio.Event()
        prompts = []
        original = None

        async def approve(request):
            prompts.append(request)
            if len(prompts) == 1:
                runtime.respond_execution_approval(request.request_id, "accept")
                return
            assert not target.exists()
            assert original.interaction_lock.locked()
            assert request.params["_meta"]["parent_call_id"] == original.terminal_info.item_id
            assert request.params["_meta"]["call_id"] != original.terminal_info.item_id
            assert request.params["requestedSchema"]["properties"]["scope"]["enum"] == ["once"]
            entered.set()
            if action == "cancel":
                runtime.respond_execution_approval(request.request_id, "cancel")
            elif action == "close":
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaned.set()
            elif action == "replace":
                # Substitute a different identity under the reviewed handle.
                manager._sessions[original.id] = replace(original)
                runtime.respond_execution_approval(request.request_id, "accept")
            elif action == "remember":
                runtime.respond_execution_approval(request.request_id, "accept", remember=True)
            else:
                raise AssertionError("unreviewable content must not reach host approval")

        runtime.set_execution_approval_handler(approve)
        try:
            await call(
                runtime,
                model,
                "exec_command",
                {
                    "cmd": "read line; printf '%s' \"$line\" > received",
                    "tty": True,
                    "login": False,
                    "yield_time_ms": 250,
                    "sandbox_permissions": "require_escalated",
                },
            )
            original = next(iter(manager._sessions.values()))
            chars = (
                "x" * 8100
                if action == "oversized"
                else "nul\0\n"
                if action == "nul"
                else "unsafe\n"
            )
            model.pending = (
                "write_stdin",
                {"session_id": original.id, "chars": chars, "yield_time_ms": 250},
            )
            work = asyncio.create_task(observe(runtime, "review the retained terminal"))
            if action == "close":
                await asyncio.wait_for(entered.wait(), 3)
                await runtime.aclose()
                assert cleaned.is_set()
            events = await asyncio.wait_for(work, 5)
            assert isinstance(
                events[-1], TurnCancelled if action in {"cancel", "close"} else TurnCompleted
            )
            assert not target.exists()
            assert len(prompts) == (1 if action in {"oversized", "nul"} else 2)
            assert not manager.approvals.router._pending and not manager._stdin_reviews
            assert not original.interaction_lock.locked()
        finally:
            if original is not None:
                if action == "replace":
                    replacement = manager._sessions.pop(original.id)
                    assert replacement is not original
                await manager._retire(original)
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("kind", ["feature_off", "ordinary", "retry"])
def test_feature_gate_and_successful_attempt_authority(tmp_path, compiler, mode, kind, monkeypatch):
    async def scenario():
        model = Model(mode)
        settings = settings_for(
            tmp_path, compiler, mode, monkeypatch, enabled=kind != "feature_off"
        )
        if kind == "retry":
            settings = replace(
                settings,
                execution_permissions=replace(
                    settings.execution_permissions, approval_policy_json='"untrusted"'
                ),
            )
        runtime = await runtime_for(tmp_path, compiler, model, settings=settings)
        manager = runtime._process_manager
        prompts = []

        async def approve(request):
            prompts.append(request)
            runtime.respond_execution_approval(request.request_id, "accept")

        runtime.set_execution_approval_handler(approve)
        command = "read line; printf 'received:%s' \"$line\""
        if kind == "retry":
            command = "touch retry-start || exit; " + command
        try:
            args = {"cmd": command, "tty": True, "login": False, "yield_time_ms": 250}
            if kind == "feature_off":
                args["sandbox_permissions"] = "require_escalated"
            await call(runtime, model, "exec_command", args)
            session = next(iter(manager._sessions.values()))
            assert session.permissions.bypassed is (kind != "ordinary")
            count = len(prompts)
            # Empty polling is exempt, even with current policy missing.
            await manager.write_stdin(session.id, "", yield_seconds=0, review_enabled=True)
            assert len(prompts) == count
            await call(
                runtime,
                model,
                "write_stdin",
                {"session_id": session.id, "chars": "hello\n", "yield_time_ms": 250},
            )
            assert len(prompts) == count + (kind == "retry")
            result = [i for i in model.requests[-1].items if isinstance(i, ToolResultItem)][-1]
            assert "received:hello" in result.content and not result.is_error
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
