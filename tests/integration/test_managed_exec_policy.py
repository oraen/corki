"""Requirements-owned command rules survive user fallback and child startup."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from corki.config import CorkiSettings
from corki.config.managed_mcp import MCPRequirementsLayer, compose_mcp_requirements
from corki.config.permissions import ExecutionPermissions
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted, WarningEvent
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.session_source import SessionSource
from corki.protocol.tools import ToolCall


@pytest.fixture
def compiler():
    value = os.environ.get("CORKI_TEST_SANDBOX_COMPILER")
    if not value or sys.platform != "darwin":
        pytest.skip("requires explicit native compiler and macOS")
    return Path(value)


def managed_rule(program="printf", decision="forbidden", justification="MANAGED_RULE"):
    return (
        "[[rules.prefix_rules]]\n"
        f"pattern = [{{token={json.dumps(program)}}}]\n"
        f"decision = {json.dumps(decision)}\n"
        f"justification = {json.dumps(justification)}\n"
    )


class Model:
    def __init__(self, mode="direct", final_answer="done"):
        self.mode, self.requests = mode, []
        self.final_answer = final_answer

    async def stream(self, request):
        self.requests.append(request)
        turn, step = request.items[-1].turn_id, new_step_id()
        if len(self.requests) == 1:
            args = {"cmd": "printf MANAGED_FIXTURE", "login": False}
            call = (
                ToolCall(new_tool_call_id(), "exec_command", args)
                if self.mode == "direct"
                else ToolCall(
                    new_tool_call_id(),
                    "exec",
                    None,
                    raw_arguments="text(await tools.exec_command(" + json.dumps(args) + "))",
                    input_kind="freeform",
                )
            )
            yield ModelCompleted((ToolCallItem(call, turn, step),))
        else:
            yield ModelCompleted((AssistantMessageItem(self.final_answer, turn, step),))

    async def aclose(self):
        pass


def runtime_for(
    root,
    compiler,
    model,
    rules,
    *,
    guardian=False,
    permissions=None,
    inherited=None,
    source_name="managed",
):
    requirements = compose_mcp_requirements(
        tuple(
            MCPRequirementsLayer(f"{source_name}-{index}", text) for index, text in enumerate(rules)
        )
    )
    return LangGraphRuntime.create(
        settings=CorkiSettings(
            working_directory=root,
            skills_enabled=False,
            tool_mode=model.mode,
            execution_permissions=permissions
            or ExecutionPermissions(compiler, root, '{"type":"read-only"}'),
        ),
        database_path=root / "state.db",
        home_path=root / "home",
        model=model,
        mcp_requirements=requirements,
        session_source=SessionSource.internal("guardian" if guardian else "memory_consolidation"),
        inherited_exec_policy=inherited,
    )


async def run(runtime, model):
    events = [e async for e in runtime.stream("check managed command policy")]
    assert isinstance(events[-1], TurnCompleted), events
    result = [i for i in model.requests[-1].items if isinstance(i, ToolResultItem)][-1]
    return events, result.content


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("case", ["forbidden", "prompt", "unmatched", "bad_user", "guardian"])
def test_managed_rule_is_effective_in_actual_loop(tmp_path, compiler, mode, case):
    async def scenario():
        home_rules = tmp_path / "home" / "rules"
        home_rules.mkdir(parents=True)
        (home_rules / "user.rules").write_text(
            "prefix_rule("
            if case == "bad_user"
            else 'prefix_rule(pattern=["printf"],decision="allow")'
        )
        program = "not-printf" if case == "unmatched" else "printf"
        decision = "prompt" if case == "prompt" else "forbidden"
        model = Model(mode)
        runtime = runtime_for(
            tmp_path,
            compiler,
            model,
            [managed_rule(program, decision)],
            guardian=case == "guardian",
        )
        try:
            events, result = await run(runtime, model)
            if case == "unmatched":
                assert "MANAGED_FIXTURE" in result and "rejected" not in result
            elif case == "prompt":
                assert "approval required by policy" in result
            else:
                assert "MANAGED_RULE" in result
            if case == "bad_user":
                assert any(
                    isinstance(e, WarningEvent) and "user.rules" in e.message for e in events
                )
            assert not runtime._process_manager._sessions
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("reverse", [False, True])
def test_lower_priority_forbidden_is_not_replaced_by_prompt(tmp_path, compiler, reverse):
    async def scenario():
        layers = [managed_rule(), managed_rule(decision="prompt")]
        if reverse:
            layers.reverse()
        model = Model()
        runtime = runtime_for(tmp_path, compiler, model, layers)
        try:
            _, result = await run(runtime, model)
            assert "MANAGED_RULE" in result
            assert "approval required" not in result
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "rules,reason",
    [
        (managed_rule(decision="allow"), "not permitted in requirements.toml"),
        ("[rules]\nprefix_rules=[]", "prefix_rules cannot be empty"),
        ('[[rules.prefix_rules]]\npattern=[]\ndecision="forbidden"', "empty pattern"),
        ('[[rules.prefix_rules]]\npattern=[{token="printf"}]', "missing a decision"),
        (
            '[[rules.prefix_rules]]\npattern=[{token="printf",any_of=["cat"]}]\ndecision="forbidden"',
            "set either token or any_of, not both",
        ),
        (managed_rule(justification=" "), "empty justification"),
    ],
)
def test_bad_managed_rules_are_fatal_not_user_rule_fallback(tmp_path, compiler, rules, reason):
    async def scenario():
        model = Model()
        runtime = runtime_for(tmp_path, compiler, model, [rules])
        try:
            with pytest.raises(ValueError, match=reason):
                await run(runtime, model)
            assert not model.requests
            assert not runtime._thread_created
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "case", ["same", "reordered", "changed_rule", "changed_source", "non_exec"]
)
def test_inherited_rules_compare_native_managed_identity(tmp_path, compiler, case):
    from dataclasses import replace

    async def scenario():
        home_rules = tmp_path / "home" / "rules"
        home_rules.mkdir(parents=True)
        source = home_rules / "user.rules"
        source.write_text('prefix_rule(pattern=["printf"],decision="forbidden")')
        original = managed_rule("cat") + managed_rule("ls")
        parent_model = Model()
        parent = runtime_for(tmp_path, compiler, parent_model, [original])
        try:
            _, result = await run(parent, parent_model)
            assert "rejected" in result
            permissions = parent._settings.execution_permissions
            inherited = permissions.exec_policy_snapshot
            assert inherited.managed_identity
            source.write_text("")
            changed = {
                "same": original,
                "reordered": managed_rule("ls") + managed_rule("cat"),
                "changed_rule": managed_rule("cat", "prompt") + managed_rule("ls"),
                "changed_source": original,
                "non_exec": 'allowed_sandbox_modes=["read-only"]\n' + original,
            }[case]
            model = Model()
            child = runtime_for(
                tmp_path,
                compiler,
                model,
                [changed],
                permissions=replace(permissions, requirements=()),
                inherited=inherited,
                source_name="other" if case == "changed_source" else "managed",
            )
            try:
                _, result = await run(child, model)
                assert ("rejected" in result) == (case in {"same", "reordered", "non_exec"})
                assert child._settings.execution_permissions.exec_policy_snapshot is not inherited
            finally:
                await child.aclose()
        finally:
            await parent.aclose()

    asyncio.run(scenario())


def test_managed_alternatives_match_command(tmp_path, compiler):
    async def scenario():
        rules = (
            '[[rules.prefix_rules]]\npattern=[{any_of=["cat","printf"]}]\n'
            'decision="forbidden"\njustification="ALTERNATIVE_RULE"'
        )
        model = Model()
        runtime = runtime_for(tmp_path, compiler, model, [rules])
        try:
            _, result = await run(runtime, model)
            assert "ALTERNATIVE_RULE" in result
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("stale_base", [False, True])
def test_real_memory_worker_retains_callers_managed_rules(tmp_path, compiler, mode, stale_base):
    from dataclasses import replace

    async def scenario():
        class Main:
            async def stream(self, request):
                yield ModelCompleted(
                    (AssistantMessageItem("parent done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        memory = Model(
            mode, json.dumps({"memory": "verified", "memory_summary": "verified", "skills": []})
        )
        requirements = compose_mcp_requirements(
            (MCPRequirementsLayer("managed-memory", managed_rule()),)
        )
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                tool_mode=mode,
                memories_consolidation_model="fixture",
                execution_permissions=ExecutionPermissions(
                    compiler, tmp_path, '{"type":"read-only"}'
                ),
            ),
            database_path=tmp_path / "state.db",
            home_path=tmp_path / "home",
            memory_root=tmp_path / "memories",
            model=Main(),
            memory_model=memory,
            mcp_requirements=requirements,
        )
        if stale_base:
            runtime._memory_service._settings = replace(
                runtime._memory_service._settings, execution_permissions=None
            )
        try:
            events = [e async for e in runtime.stream("parent continues")]
            assert isinstance(events[-1], TurnCompleted)
            report = await runtime._memory_service.wait()
            assert report.consolidated and not report.failed, runtime._memory_service.warnings
            assert len(memory.requests) == 2
            result = [i for i in memory.requests[-1].items if isinstance(i, ToolResultItem)][-1]
            assert "MANAGED_RULE" in result.content
            assert "verified" in (tmp_path / "memories" / "MEMORY.md").read_text()
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
