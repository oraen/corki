"""Rule-driven execution approval flows through the real model/tool Runtime."""

import asyncio
import json
import os
import shlex
import sys
from pathlib import Path

import pytest

from corki.code_mode.service import CodeModeService
from corki.config import CorkiPaths, CorkiSettings
from corki.config.exec_policy import ExecPolicySource
from corki.config.permissions import ExecutionPermissions
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCancelled, TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall


@pytest.fixture
def compiler():
    value = os.environ.get("CORKI_TEST_SANDBOX_COMPILER")
    if not value or sys.platform != "darwin":
        pytest.skip("requires explicit native compiler and macOS")
    return Path(value)


class Model:
    def __init__(self, mode, command="printf APPROVAL_EXECUTED", arguments=None):
        self.mode, self.requests = mode, []
        self.command = command
        self.arguments = arguments or {}

    async def stream(self, request):
        self.requests.append(request)
        if len(self.requests) % 2:
            args = {"cmd": self.command, "login": False, **self.arguments}
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
        else:
            yield ModelCompleted(())

    async def aclose(self):
        pass


async def runtime_for(tmp_path, compiler, model, policy='"on-request"', rule=None, requirements=()):
    if model.mode == "code_mode_only" and not CodeModeService.available():
        pytest.skip("install corki[code-mode]")
    return await LangGraphRuntime.acreate(
        settings=CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            tool_mode=model.mode,
            execution_permissions=ExecutionPermissions(
                compiler,
                tmp_path,
                '{"type":"read-only"}',
                exec_policy_sources=(
                    ExecPolicySource(
                        str(tmp_path / "host.rules"),
                        rule
                        if rule is not None
                        else 'prefix_rule(pattern=["printf"], decision="prompt", '
                        'justification="REVIEW_FIXTURE")',
                    ),
                ),
                approval_policy_json=policy,
                requirements=requirements,
            ),
        ),
        home_path=tmp_path / "home",
        database_path=tmp_path / "sessions.db",
        model=model,
    )


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize(
    "case",
    [
        "accept",
        "no_justification",
        "decline",
        "missing",
        "never_allow",
        "granular_allow",
        "explicit_allow",
        "missing_flag",
        "default_flag",
    ],
)
def test_model_escalation_reaches_native_admission_and_real_execution(
    tmp_path, compiler, mode, case
):
    async def scenario():
        target = tmp_path / "created-by-command"
        arguments = {"sandbox_permissions": "require_escalated", "justification": "CREATE_FIXTURE"}
        if case == "no_justification":
            arguments.pop("justification")
        elif case == "missing_flag":
            arguments.pop("sandbox_permissions")
        elif case == "default_flag":
            arguments["sandbox_permissions"] = "use_default"
        policy = '"on-request"'
        if case == "never_allow":
            policy = '"never"'
        elif case == "granular_allow":
            policy = json.dumps(
                {"granular": {"rules": True, "sandbox_approval": False, "mcp_elicitations": False}}
            )
        rule = 'prefix_rule(pattern=["mkdir"], decision="allow")' if case.endswith("allow") else ""
        model = Model(mode, "mkdir " + shlex.quote(str(target)), arguments)
        runtime = await runtime_for(tmp_path, compiler, model, policy, rule)
        prompts = []

        async def approve(request):
            prompts.append(request)
            assert not target.exists() and not runtime._process_manager._sessions
            assert (
                request.params["_meta"]["tool_params"]["sandbox_permissions"] == "require_escalated"
            )
            if case != "no_justification":
                assert request.params["message"] == "CREATE_FIXTURE"
            runtime.respond_execution_approval(
                request.request_id, "decline" if case == "decline" else "accept"
            )

        if case != "missing":
            runtime.set_execution_approval_handler(approve)
        try:
            events = [event async for event in runtime.stream("request execution")]
            assert isinstance(events[-1], TurnCompleted)
            output = [
                item for item in model.requests[-1].items if isinstance(item, ToolResultItem)
            ][-1]
            if case in {"accept", "no_justification", "explicit_allow"}:
                assert target.is_dir() and not output.is_error, output.content
            else:
                assert not target.exists(), output.content
                expected = {
                    "decline": "execution approval rejected",
                    "missing": "no host approval",
                    "never_allow": "cannot ask for escalated permissions",
                    "granular_allow": "cannot ask for escalated permissions",
                    "missing_flag": "requires an explicit `sandbox_permissions`",
                    "default_flag": "Operation not permitted",
                }[case]
                assert expected in output.content, output.content
            assert len(prompts) == (1 if case in {"accept", "no_justification", "decline"} else 0)
            assert not runtime._process_manager._sessions
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("denied", [False, True])
def test_approved_model_escalation_preserves_managed_read_denials(tmp_path, compiler, mode, denied):
    from corki.config.managed_mcp import MCPRequirementsLayer, compose_mcp_requirements

    async def scenario():
        secret = tmp_path / "fixture.txt"
        secret.write_text("MODEL_ESCALATION_FIXTURE")
        requirements = compose_mcp_requirements(
            (
                MCPRequirementsLayer(
                    "host",
                    "[permissions.filesystem]\ndeny_read="
                    + json.dumps([str(secret)] if denied else []),
                ),
            )
        )
        model = Model(
            mode, "cat " + shlex.quote(str(secret)), {"sandbox_permissions": "require_escalated"}
        )
        runtime = await runtime_for(
            tmp_path, compiler, model, rule="", requirements=requirements.execution
        )
        prompts = []

        async def approve(request):
            prompts.append(request)
            runtime.respond_execution_approval(request.request_id, "accept")

        runtime.set_execution_approval_handler(approve)
        try:
            events = [event async for event in runtime.stream("read controlled fixture")]
            assert isinstance(events[-1], TurnCompleted)
            output = [
                item for item in model.requests[-1].items if isinstance(item, ToolResultItem)
            ][-1]
            assert len(prompts) == 1, output.content
            assert ("MODEL_ESCALATION_FIXTURE" in output.content) is not denied, output.content
            if denied:
                assert "Operation not permitted" in output.content
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("decision", ["once", "session", "decline", "cancel", "missing"])
def test_rule_approval_executes_only_with_host_consent(tmp_path, compiler, mode, decision):
    async def scenario():
        model, prompts = Model(mode), []
        runtime = await runtime_for(tmp_path, compiler, model)

        async def approve(request):
            prompts.append(request)
            assert request.kind == "shell_approval"
            assert "REVIEW_FIXTURE" in request.params["message"]
            assert not runtime._process_manager._sessions
            runtime.respond_execution_approval(
                request.request_id,
                "accept" if decision in {"once", "session"} else decision,
                remember=decision == "session",
            )

        if decision != "missing":
            runtime.set_execution_approval_handler(approve)
        try:
            for _ in range(1 if decision == "cancel" else 2):
                events = []
                try:
                    async for event in runtime.stream("check execution approval"):
                        events.append(event)
                except asyncio.CancelledError:
                    assert decision == "cancel" and not asyncio.current_task().cancelling()
                if decision == "cancel":
                    assert isinstance(events[-1], TurnCancelled), events[-1]
                    assert len(model.requests) == 1
                    continue
                assert isinstance(events[-1], TurnCompleted), events[-1]
                result = [
                    item for item in model.requests[-1].items if isinstance(item, ToolResultItem)
                ][-1]
                if decision in {"once", "session"}:
                    assert "APPROVAL_EXECUTED" in result.content and not result.is_error
                else:
                    assert result.is_error
                    assert "approval" in result.content.lower()
            assert len(prompts) == (
                0 if decision == "missing" else 1 if decision in {"session", "cancel"} else 2
            )
            assert len({request.request_id for request in prompts}) == len(prompts)
            assert not runtime._process_manager._sessions
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("denied", [False, True])
def test_session_consent_keeps_original_permission_intent_and_no_sticky_escalation(
    tmp_path, compiler, mode, denied
):
    from corki.config.managed_mcp import MCPRequirementsLayer, compose_mcp_requirements

    async def scenario():
        target = tmp_path / "cache-fixture"
        command_name, requirements = "touch", ()
        if denied:
            target.write_text("DENIED_FIXTURE_CONTENT")
            command_name = "cat"
            requirements = compose_mcp_requirements(
                (
                    MCPRequirementsLayer(
                        "host", "[permissions.filesystem]\ndeny_read=" + json.dumps([str(target)])
                    ),
                )
            ).execution
        model = Model(mode, command_name + " " + shlex.quote(str(target)))
        runtime = await runtime_for(
            tmp_path,
            compiler,
            model,
            rule=(
                f'prefix_rule(pattern=["{command_name}"], decision="prompt", '
                'justification="RULE_REASON")'
            ),
            requirements=requirements,
        )
        prompts = []

        async def approve(request):
            prompts.append(request)
            assert "RULE_REASON" in request.params["message"]
            assert "MODEL_REASON" not in request.params["message"]
            runtime.respond_execution_approval(request.request_id, "accept", remember=True)

        runtime.set_execution_approval_handler(approve)
        try:
            for index, intent in enumerate(["use_default", "require_escalated"] * 2):
                model.arguments = {"sandbox_permissions": intent, "justification": "MODEL_REASON"}
                events = [event async for event in runtime.stream("check per-command permissions")]
                assert isinstance(events[-1], TurnCompleted)
                output = [
                    item for item in model.requests[-1].items if isinstance(item, ToolResultItem)
                ][-1]
                if denied or intent == "use_default":
                    assert "Operation not permitted" in output.content, output.content
                    assert "DENIED_FIXTURE_CONTENT" not in output.content
                else:
                    assert target.is_file() and "Operation not permitted" not in output.content
                assert len(prompts) == min(index + 1, 2)
            assert [p.params["_meta"]["tool_params"]["sandbox_permissions"] for p in prompts] == [
                "use_default",
                "require_escalated",
            ]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_memory_worker_derivation_does_not_inherit_interactive_review(tmp_path, compiler, mode):
    from corki.memory.permissions import MemoryPermissionSnapshot

    async def scenario():
        if mode == "code_mode_only" and not CodeModeService.available():
            pytest.skip("install corki[code-mode]")
        root = tmp_path / "memory"
        root.mkdir()
        parent = ExecutionPermissions(
            compiler,
            tmp_path,
            '{"type":"read-only"}',
            approval_policy_json='"on-request"',
            exec_policy_sources=(
                ExecPolicySource(
                    str(tmp_path / "parent.rules"),
                    'prefix_rule(pattern=["printf"], decision="prompt")',
                ),
            ),
        )
        worker_permissions = await MemoryPermissionSnapshot(parent).for_worker(root)
        assert worker_permissions.approval_policy_json == '"never"'
        model = Model(mode)
        worker = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=root,
                skills_enabled=False,
                tool_mode=mode,
                execution_permissions=worker_permissions,
            ),
            home_path=tmp_path / "home",
            database_path=tmp_path / "worker.db",
            model=model,
        )
        try:
            events = [event async for event in worker.stream("background memory command")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            output = [
                item for item in model.requests[-1].items if isinstance(item, ToolResultItem)
            ][-1]
            assert output.is_error and "AskForApproval is set to Never" in output.content
            assert not worker._process_manager.approvals.router._pending
        finally:
            await worker.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "case",
    [
        "never_dangerous",
        "request_dangerous",
        "granular_dangerous",
        "request_plain",
        "untrusted_plain",
    ],
)
def test_native_approval_fallback_without_executing_command(tmp_path, compiler, case):
    from corki.execution.approvals import ExecutionApprovals
    from corki.execution.backend import sandbox_command

    async def scenario():
        policy = {
            "never_dangerous": "never",
            "request_dangerous": "on-request",
            "request_plain": "on-request",
            "untrusted_plain": "untrusted",
            "granular_dangerous": {
                "granular": {"rules": True, "sandbox_approval": False, "mcp_elicitations": False}
            },
        }[case]
        approvals, prompts = ExecutionApprovals(), []

        async def decline(request):
            prompts.append(request)
            approvals.router.respond("local-shell", request.request_id, "decline")

        approvals.router.handler = decline
        permissions = ExecutionPermissions(
            compiler, tmp_path, '{"type":"read-only"}', approval_policy_json=json.dumps(policy)
        )
        # This API compiles a command only; no model-command process is spawned.
        argv = (
            ["rm", "-f", str(tmp_path / "unused")]
            if case.endswith("dangerous")
            else ["printf", "unused"]
        )
        if case == "request_plain":
            assert await sandbox_command(permissions, argv, tmp_path, approvals=approvals)
        else:
            expected = (
                "rm -f style"
                if case in {"never_dangerous", "granular_dangerous"}
                else "execution approval rejected"
            )
            with pytest.raises(ValueError, match=expected):
                await sandbox_command(permissions, argv, tmp_path, approvals=approvals)
        assert len(prompts) == int(case in {"request_dangerous", "untrusted_plain"})

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("action", ["cancel", "close"])
@pytest.mark.parametrize("intent", ["use_default", "require_escalated"])
def test_pending_approval_is_cancelled_and_late_response_rejected(
    tmp_path, compiler, mode, action, intent
):
    async def scenario():
        model = Model(mode, arguments={"sandbox_permissions": intent})
        runtime = await runtime_for(tmp_path, compiler, model)
        entered, dismissed = asyncio.Event(), asyncio.Event()
        prompts, events = [], []

        async def approve(request):
            prompts.append(request)
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                dismissed.set()

        runtime.set_execution_approval_handler(approve)

        async def consume():
            try:
                async for event in runtime.stream("cancel review"):
                    events.append(event)
            except asyncio.CancelledError:
                pass

        consumer = asyncio.create_task(consume())
        try:
            async with asyncio.timeout(5):
                await entered.wait()
                with pytest.raises(ValueError, match="Unknown"):
                    runtime.respond_mcp_elicitation("local-shell", prompts[0].request_id, "accept")
                if action == "cancel":
                    await runtime.cancel_active()
                else:
                    await runtime.aclose()
                await consumer
            assert dismissed.is_set() and len(model.requests) == 1
            assert isinstance(events[-1], TurnCancelled)
            assert not runtime._process_manager._sessions
            assert not runtime._process_manager.approvals.router._pending
            with pytest.raises(ValueError, match="Unknown"):
                runtime.respond_execution_approval(prompts[0].request_id, "accept")
        finally:
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("intent", ["use_default", "require_escalated"])
def test_cli_approval_respects_requested_sandbox_intent(tmp_path, compiler, mode, intent):
    from corki.cli.application import CorkiApplication

    async def scenario():
        target = tmp_path / "cli-fixture"
        model = Model(mode, f"mkdir {target}", {"sandbox_permissions": intent})
        runtime = await runtime_for(
            tmp_path,
            compiler,
            model,
            rule='prefix_rule(pattern=["mkdir"], decision="prompt")',
        )
        prompts = []

        class UI:
            async def read_elicitation(self, request):
                prompts.append(request)
                return "accept", {"remember": False}

        CorkiApplication(
            CorkiSettings(working_directory=tmp_path),
            CorkiPaths.from_home(tmp_path / "home"),
            runtime,
            UI(),
        )
        try:
            events = [event async for event in runtime.stream("check command approval")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(prompts) == 1 and prompts[0].kind == "shell_approval"
            assert target.exists() is (intent == "require_escalated")
            output = [
                item for item in model.requests[-1].items if isinstance(item, ToolResultItem)
            ][-1]
            assert ("Operation not permitted" in output.content) is (intent == "use_default")
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("allowed", [False, True])
def test_granular_rule_prompt_is_independent_of_sandbox_prompt_switch(tmp_path, compiler, allowed):
    async def scenario():
        model = Model("direct")
        runtime = await runtime_for(
            tmp_path,
            compiler,
            model,
            policy=json.dumps(
                {
                    "granular": {
                        "rules": allowed,
                        "sandbox_approval": not allowed,
                        "mcp_elicitations": False,
                    }
                }
            ),
        )
        prompts = []

        async def approve(request):
            prompts.append(request)
            runtime.respond_execution_approval(request.request_id, "accept")

        runtime.set_execution_approval_handler(approve)
        try:
            events = [event async for event in runtime.stream("granular rules")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            output = [
                item for item in model.requests[-1].items if isinstance(item, ToolResultItem)
            ][-1]
            assert output.is_error == (not allowed)
            assert len(prompts) == int(allowed)
            assert ("Granular.rules is false" in output.content) == (not allowed)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
