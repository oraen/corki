"""Native rule proposals must become authority only after a host saves the rule."""

import asyncio
import json
import os
import shlex
import sys
from pathlib import Path

import pytest

from corki.cli.application import CorkiApplication
from corki.code_mode.service import CodeModeService
from corki.config import CorkiPaths, CorkiSettings
from corki.config.permissions import ExecutionPermissions
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCancelled, TurnCompleted, WarningEvent
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.model_authority import ModelAuthority
from corki.protocol.tools import ToolCall


@pytest.fixture
def compiler():
    value = os.environ.get("CORKI_TEST_SANDBOX_COMPILER")
    if not value or sys.platform != "darwin":
        pytest.skip("requires explicit native compiler and macOS")
    return Path(value)


class Model:
    def __init__(self, mode, commands):
        self.mode, self.commands, self.requests = mode, commands, []

    async def stream(self, request):
        self.requests.append(request)
        if len(self.requests) > len(self.commands):
            yield ModelCompleted(())
            return
        args = {"login": False, **self.commands[len(self.requests) - 1]}
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


def runtime_for(tmp_path, compiler, model, *, cyber=False):
    if model.mode != "direct" and not CodeModeService.available():
        pytest.skip("install corki[code-mode]")
    return LangGraphRuntime.create(
        settings=CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            tool_mode=model.mode,
            model="fixture",
            model_contexts=(
                ModelContextInfo(model="fixture", activation_authority=ModelAuthority(cyber=cyber)),
            ),
            execution_permissions=ExecutionPermissions(
                compiler, tmp_path, '{"type":"read-only"}', approval_policy_json='"on-request"'
            ),
        ),
        home_path=tmp_path / "home",
        database_path=tmp_path / "state.db",
        model=model,
    )


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("host", ["api", "cli"])
@pytest.mark.parametrize("scope", ["once", "session", "rule"])
def test_rule_approval_affects_future_commands_and_cold_runtime_only_when_saved(
    tmp_path, compiler, mode, host, scope
):
    async def scenario():
        (tmp_path / "home").mkdir()
        first, second, cold = (tmp_path / name for name in ["first", "second", "cold"])
        policy = tmp_path / "home" / "rules" / "default.rules"
        model = Model(
            mode,
            [
                {
                    "cmd": "touch " + shlex.quote(str(first)),
                    "sandbox_permissions": "require_escalated",
                    "prefix_rule": ["touch"],
                },
                {"cmd": "touch " + shlex.quote(str(second))},
            ],
        )
        runtime = runtime_for(tmp_path, compiler, model)
        prompts = []

        async def respond(request):
            prompts.append(request)
            assert request.params["_meta"]["execpolicy_amendment"] == ["touch"]
            assert request.params["_meta"]["tool_params"]["proposed_execpolicy_amendment"] == [
                "touch"
            ]
            assert not policy.exists() and not first.exists()
            runtime.respond_execution_approval(
                request.request_id,
                "accept",
                remember=scope == "session",
                execpolicy_amendment=["touch"] if scope == "rule" else None,
            )

        class UI:
            async def read_elicitation(self, request):
                prompts.append(request)
                assert request.params["_meta"]["execpolicy_amendment"] == ["touch"]
                assert request.params["_meta"]["tool_params"]["proposed_execpolicy_amendment"] == [
                    "touch"
                ]
                return "accept", {"scope": scope}

        if host == "api":
            runtime.set_execution_approval_handler(respond)
        else:
            CorkiApplication(
                CorkiSettings(working_directory=tmp_path),
                CorkiPaths.from_home(tmp_path / "home"),
                runtime,
                UI(),
            )
        try:
            events = [event async for event in runtime.stream("review and save only if selected")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(prompts) == 1 and first.is_file()
            assert second.exists() is (scope == "rule")
            assert policy.exists() is (scope == "rule")
            if scope == "rule":
                assert policy.read_text() == 'prefix_rule(pattern=["touch"], decision="allow")\n'
                assert not runtime._process_manager.approvals._session
            assert not any(isinstance(event, WarningEvent) for event in events)
        finally:
            await runtime.aclose()

        restored_model = Model(mode, [{"cmd": "touch " + shlex.quote(str(cold))}])
        restored = runtime_for(tmp_path, compiler, restored_model)
        try:
            events = [event async for event in restored.stream("cold policy load")]
            assert isinstance(events[-1], TurnCompleted)
            assert cold.exists() is (scope == "rule")
            assert not restored._process_manager.approvals.router._pending
        finally:
            await restored.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_cyber_model_ignores_user_allow_prefixes(tmp_path, compiler, mode):
    async def scenario():
        rules = tmp_path / "home" / "rules"
        rules.mkdir(parents=True)
        (rules / "default.rules").write_text('prefix_rule(pattern=["touch"], decision="allow")\n')
        target = tmp_path / "cyber-fixture"
        model = Model(mode, [{"cmd": "touch " + shlex.quote(str(target))}])
        runtime = runtime_for(tmp_path, compiler, model, cyber=True)
        try:
            events = [event async for event in runtime.stream("honor model-owned rule policy")]
            assert isinstance(events[-1], TurnCompleted)
            assert not target.exists()
            output = [
                item for item in model.requests[-1].items if isinstance(item, ToolResultItem)
            ][-1]
            assert "Operation not permitted" in output.content
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("failure", ["io", "empty"])
def test_failed_rule_save_warns_but_preserves_only_current_approval(
    tmp_path, compiler, mode, failure
):
    async def scenario():
        home = tmp_path / "home"
        home.mkdir()
        policy = home / "rules" / "default.rules"
        if failure == "io":
            policy.mkdir(parents=True)
        first, second = tmp_path / "approved", tmp_path / "not-authorized"
        model = Model(
            mode,
            [
                {
                    "cmd": "touch " + shlex.quote(str(first)),
                    "sandbox_permissions": "require_escalated",
                    "prefix_rule": ["touch"],
                },
                {"cmd": "touch " + shlex.quote(str(second))},
            ],
        )
        runtime = runtime_for(tmp_path, compiler, model)

        async def respond(request):
            runtime.respond_execution_approval(
                request.request_id,
                "accept",
                execpolicy_amendment=[] if failure == "empty" else ["touch"],
            )

        runtime.set_execution_approval_handler(respond)
        try:
            events = [event async for event in runtime.stream("save failure is not current denial")]
            assert isinstance(events[-1], TurnCompleted)
            assert first.is_file() and not second.exists()
            warnings = [e.message for e in events if isinstance(e, WarningEvent)]
            assert any("Failed to apply execpolicy amendment" in message for message in warnings)
            assert not runtime._process_manager.approvals.rules.prefixes
            assert not runtime._process_manager.approvals._session
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("complex_script", [False, True])
def test_native_canonical_cache_reuses_simple_tokens_but_preserves_complex_script_text(
    tmp_path, compiler, mode, complex_script
):
    async def scenario():
        rules = tmp_path / "home" / "rules"
        rules.mkdir(parents=True)
        (rules / "default.rules").write_text('prefix_rule(pattern=["printf"], decision="prompt")\n')
        commands = ["printf 'CACHE'", "printf   CACHE"]
        if complex_script:
            commands = ["printf A && printf B", "printf A  && printf B"]
        model = Model(mode, [{"cmd": command} for command in commands])
        runtime = runtime_for(tmp_path, compiler, model)
        prompts = []

        async def respond(request):
            prompts.append(request)
            runtime.respond_execution_approval(request.request_id, "accept", remember=True)

        runtime.set_execution_approval_handler(respond)
        try:
            events = [event async for event in runtime.stream("native approval identity")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(prompts) == (2 if complex_script else 1)
            outputs = [
                item for item in model.requests[-1].items if isinstance(item, ToolResultItem)
            ]
            assert len(outputs) == 2 and all(not item.is_error for item in outputs)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_cancel_joins_authorized_rule_update_before_reporting_turn_terminal(
    tmp_path, compiler, mode, monkeypatch
):
    from corki.execution import rules

    async def scenario():
        (tmp_path / "home").mkdir()
        target = tmp_path / "must-not-execute"
        model = Model(
            mode,
            [
                {
                    "cmd": "touch " + shlex.quote(str(target)),
                    "sandbox_permissions": "require_escalated",
                    "prefix_rule": ["touch"],
                }
            ],
        )
        runtime = runtime_for(tmp_path, compiler, model)
        entered, release = asyncio.Event(), asyncio.Event()
        original = rules.run_owned

        async def held(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        monkeypatch.setattr(rules, "run_owned", held)

        async def respond(request):
            runtime.respond_execution_approval(
                request.request_id, "accept", execpolicy_amendment=["touch"]
            )

        runtime.set_execution_approval_handler(respond)
        events = []

        async def consume():
            try:
                async for event in runtime.stream("save then interrupt"):
                    events.append(event)
            except asyncio.CancelledError:
                pass

        consumer = asyncio.create_task(consume())
        try:
            async with asyncio.timeout(10):
                await entered.wait()
                await runtime.cancel_active()
                await asyncio.sleep(0)
                assert not runtime._active_run.done.is_set()
                release.set()
                await consumer
            assert isinstance(events[-1], TurnCancelled) and len(model.requests) == 1
            assert not target.exists() and not runtime._process_manager._starting
            assert runtime._process_manager.approvals.rules.prefixes == (("touch",),)
            policy = tmp_path / "home" / "rules" / "default.rules"
            assert policy.read_text() == 'prefix_rule(pattern=["touch"], decision="allow")\n'
        finally:
            release.set()
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "command,prefix,rule,honor,expected",
    [
        ("printf x", ["printf"], "", True, ("printf",)),
        ("printf x", [], "", True, ("printf", "x")),
        ("printf x", ["bash"], "", True, ("printf", "x")),
        ("printf x", ["echo"], "", True, ("printf", "x")),
        ("printf x && printf y", ["printf"], "", True, ("printf",)),
        ("printf x && echo y", ["printf"], "", True, ("printf", "x")),
        ("printf x", ["printf"], 'prefix_rule(pattern=["printf"], decision="prompt")', True, None),
        ("printf x", ["printf"], 'prefix_rule(pattern=["printf"], decision="allow")', False, None),
    ],
)
def test_native_suggestions_filter_requested_prefix_then_use_native_fallback(
    tmp_path, compiler, command, prefix, rule, honor, expected
):
    from corki.config.exec_policy import ExecPolicySource
    from corki.execution.backend import _compile

    async def scenario():
        permissions = ExecutionPermissions(
            compiler,
            tmp_path,
            '{"type":"read-only"}',
            approval_policy_json='"on-request"',
            exec_policy_sources=(ExecPolicySource(str(tmp_path / "fixture.rules"), rule),),
        )
        compiled = await _compile(
            permissions,
            ["/bin/bash", "-c", command],
            tmp_path,
            check_exec_policy=True,
            sandbox_permissions="require_escalated",
            prefix_rule=prefix,
            honor_allow_prefix_rules=honor,
        )
        # Policy compilation only: none of these argv values are executed.
        assert compiled.approval is not None
        assert compiled.approval.proposed_execpolicy_amendment == expected

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_live_approved_rule_survives_old_user_parse_fallback(tmp_path, compiler, mode):
    async def scenario():
        rules = tmp_path / "home" / "rules"
        rules.mkdir(parents=True)
        (rules / "broken.rules").write_text("not a valid rule(")
        first, second = tmp_path / "first", tmp_path / "second"
        model = Model(
            mode,
            [
                {
                    "cmd": "touch " + shlex.quote(str(first)),
                    "sandbox_permissions": "require_escalated",
                    "prefix_rule": ["touch"],
                },
                {"cmd": "touch " + shlex.quote(str(second))},
            ],
        )
        runtime = runtime_for(tmp_path, compiler, model)

        async def respond(request):
            runtime.respond_execution_approval(
                request.request_id, "accept", execpolicy_amendment=["touch"]
            )

        runtime.set_execution_approval_handler(respond)
        try:
            events = [event async for event in runtime.stream("append after user-rule fallback")]
            assert isinstance(events[-1], TurnCompleted)
            assert first.is_file() and second.is_file()
            assert any(isinstance(e, WarningEvent) and "parse" in e.message for e in events)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_parallel_runtimes_persist_the_same_rule_without_duplicate_lines(tmp_path, compiler):
    async def scenario():
        (tmp_path / "home").mkdir()
        targets = [tmp_path / "one", tmp_path / "two"]
        runtimes = [
            runtime_for(
                tmp_path,
                compiler,
                Model(
                    "direct",
                    [
                        {
                            "cmd": "touch " + shlex.quote(str(target)),
                            "sandbox_permissions": "require_escalated",
                            "prefix_rule": ["touch"],
                        }
                    ],
                ),
            )
            for target in targets
        ]
        ready, prompts = asyncio.Event(), []

        for runtime in runtimes:

            async def respond(request, owner=runtime):
                prompts.append(request)
                if len(prompts) == 2:
                    ready.set()
                await ready.wait()
                owner.respond_execution_approval(
                    request.request_id, "accept", execpolicy_amendment=["touch"]
                )

            runtime.set_execution_approval_handler(respond)

        async def run(runtime):
            return [event async for event in runtime.stream("save concurrently")]

        try:
            async with asyncio.timeout(10):
                events = await asyncio.gather(*(run(runtime) for runtime in runtimes))
            assert all(isinstance(result[-1], TurnCompleted) for result in events)
            assert len(prompts) == 2 and all(target.exists() for target in targets)
            assert (tmp_path / "home" / "rules" / "default.rules").read_text() == (
                'prefix_rule(pattern=["touch"], decision="allow")\n'
            )
        finally:
            await asyncio.gather(*(runtime.aclose() for runtime in runtimes))

    asyncio.run(scenario())
