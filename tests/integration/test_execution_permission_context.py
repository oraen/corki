"""Model-visible authority must track the policy used by the real Runtime."""

import asyncio
import json
import os
import sys
from pathlib import Path
from xml.etree import ElementTree

import pytest

from corki.code_mode.service import CodeModeService
from corki.config import CorkiSettings
from corki.config.execution_requirements import ExecutionRequirementsLayer
from corki.config.permissions import ExecutionPermissions
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import ContextCompacted, TurnCompleted, WarningEvent
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    ContextRole,
    ToolCallItem,
    new_step_id,
)
from corki.protocol.model_authority import ModelAuthority
from corki.protocol.tools import ToolCall


@pytest.fixture
def compiler():
    value = os.environ.get("CORKI_TEST_SANDBOX_COMPILER")
    if not value or sys.platform != "darwin":
        pytest.skip("requires explicit native compiler and macOS")
    return Path(value)


class Model:
    def __init__(self, mode, commands=()):
        self.mode, self.commands, self.requests = mode, commands, []

    async def stream(self, request):
        self.requests.append(request)
        index = len(self.requests) - 1
        if index >= len(self.commands):
            yield ModelCompleted(())
            return
        args = {"login": False, **self.commands[index]}
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


async def runtime_for(
    tmp_path,
    compiler,
    model,
    approval="on-request",
    *,
    cyber=False,
    include=True,
    profile=None,
    requirements=(),
    thread_id=None,
):
    if model.mode != "direct" and not CodeModeService.available():
        pytest.skip("install corki[code-mode]")
    return await LangGraphRuntime.acreate(
        settings=CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            tool_mode=model.mode,
            include_permissions_instructions=include,
            model="fixture",
            model_contexts=(
                ModelContextInfo(model="fixture", activation_authority=ModelAuthority(cyber=cyber)),
            ),
            execution_permissions=ExecutionPermissions(
                compiler,
                tmp_path,
                json.dumps(profile or {"type": "read-only"}),
                requirements=requirements,
                approval_policy_json=json.dumps(approval),
            ),
        ),
        home_path=tmp_path / "home",
        database_path=tmp_path / "state.db",
        model=model,
        thread_id=thread_id,
    )


def fragments(request, kind):
    return [
        item
        for item in request.items
        if isinstance(item, ContextItem) and item.content_kind == kind
    ]


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("approval", ["never", "on-request"])
def test_initial_request_describes_actual_execution_authority(tmp_path, compiler, mode, approval):
    async def scenario():
        model = Model(mode)
        runtime = await runtime_for(tmp_path, compiler, model, approval)
        try:
            events = [event async for event in runtime.stream("inspect current execution policy")]
            assert isinstance(events[-1], TurnCompleted)
            items = fragments(model.requests[0], "permissions.instructions")
            assert len(items) == 1
            assert items[0].role is ContextRole.DEVELOPER
            text = items[0].content
            assert "`sandbox_mode` is `read-only`" in text
            assert "Network access is restricted" in text
            if approval == "never":
                assert "Approval policy is currently never" in text
                assert "# Escalation Requests" not in text
            else:
                assert "# Escalation Requests" in text
                assert "prefix_rule" in text and "require_escalated" in text
            assert "# request_permissions Tool" not in text
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_saved_rule_is_incremental_context_not_repeated_full_instructions(tmp_path, compiler, mode):
    async def scenario():
        (tmp_path / "home").mkdir()
        model = Model(
            mode,
            [
                {
                    "cmd": "printf saved",
                    "sandbox_permissions": "require_escalated",
                    "prefix_rule": ["printf"],
                },
                {"cmd": "printf next"},
            ],
        )
        runtime = await runtime_for(tmp_path, compiler, model)

        async def approve(request):
            runtime.respond_execution_approval(
                request.request_id, "accept", execpolicy_amendment=["printf"]
            )

        runtime.set_execution_approval_handler(approve)
        try:
            events = [event async for event in runtime.stream("save a prefix then use it")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(model.requests) == 3
            initial, second, third = model.requests
            assert len(fragments(initial, "permissions.instructions")) == 1
            assert not fragments(initial, "permissions.approved_command_prefix_saved")
            notices = fragments(second, "permissions.approved_command_prefix_saved")
            assert len(notices) == 1
            assert notices[0].role is ContextRole.DEVELOPER
            assert "Approved command prefix saved:" in notices[0].content
            assert '["printf"]' in notices[0].content
            assert len(fragments(second, "permissions.instructions")) == 1
            assert fragments(third, "permissions.approved_command_prefix_saved") == notices
            assert (tmp_path / "home/rules/default.rules").read_text() == (
                'prefix_rule(pattern=["printf"], decision="allow")\n'
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("cyber", [False, True])
def test_startup_prefix_context_uses_model_filtered_policy(tmp_path, compiler, mode, cyber):
    async def scenario():
        rules = tmp_path / "home/rules"
        rules.mkdir(parents=True)
        (rules / "default.rules").write_text('prefix_rule(pattern=["printf"], decision="allow")\n')
        model = Model(mode)
        runtime = await runtime_for(tmp_path, compiler, model, cyber=cyber)
        try:
            events = [event async for event in runtime.stream("inspect effective saved rules")]
            assert isinstance(events[-1], TurnCompleted)
            items = fragments(model.requests[0], "permissions.instructions")
            assert len(items) == 1
            assert ('["printf"]' in items[0].content) is (not cyber)
            assert ("## Approved command prefixes" in items[0].content) is (not cyber)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_compact_permissions_only_notify_new_prefixes(tmp_path, compiler, mode):
    async def scenario():
        rules = tmp_path / "home/rules"
        rules.mkdir(parents=True)
        (rules / "default.rules").write_text('prefix_rule(pattern=["echo"], decision="allow")\n')
        model = Model(
            mode,
            [
                {
                    "cmd": "printf saved",
                    "sandbox_permissions": "require_escalated",
                    "prefix_rule": ["printf"],
                }
            ],
        )
        runtime = await runtime_for(tmp_path, compiler, model, include=False)

        async def approve(request):
            runtime.respond_execution_approval(
                request.request_id, "accept", execpolicy_amendment=["printf"]
            )

        runtime.set_execution_approval_handler(approve)
        try:
            events = [event async for event in runtime.stream("save a rule in compact mode")]
            assert isinstance(events[-1], TurnCompleted)
            first, second = model.requests
            assert not fragments(first, "permissions.instructions")
            assert not fragments(first, "permissions.approved_command_prefix_saved")
            assert not fragments(second, "permissions.instructions")
            notices = fragments(second, "permissions.approved_command_prefix_saved")
            assert len(notices) == 1
            assert notices[0].content == 'Approved command prefix saved:\n- ["printf"]'
            events = [event async for event in runtime.stream("another turn with unchanged rules")]
            assert isinstance(events[-1], TurnCompleted)
            assert (
                fragments(model.requests[-1], "permissions.approved_command_prefix_saved")
                == notices
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_default_runtime_describes_native_read_only_policy(tmp_path, mode):
    async def scenario():
        if mode != "direct" and not CodeModeService.available():
            pytest.skip("install corki[code-mode]")
        model = Model(mode)
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, tool_mode=mode
            ),
            home_path=tmp_path / "home",
            database_path=tmp_path / "state.db",
            model=model,
        )
        try:
            events = [event async for event in runtime.stream("inspect default authority")]
            assert isinstance(events[-1], TurnCompleted)
            items = fragments(model.requests[0], "permissions.instructions")
            assert len(items) == 1
            assert "`sandbox_mode` is `read-only`" in items[0].content
            assert "Network access is restricted" in items[0].content
            assert runtime._settings.execution_permissions.approval_policy_json == '"on-request"'
            assert "require_escalated" in items[0].content
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_context_renders_managed_denied_reads_and_effective_writable_roots(
    tmp_path, compiler, mode
):
    async def scenario():
        blocked = tmp_path / "blocked"
        blocked.mkdir()
        requirements = (
            ExecutionRequirementsLayer(
                "system", json.dumps({"permissions": {"filesystem": {"deny_read": [str(blocked)]}}})
            ),
        )
        model = Model(mode)
        runtime = await runtime_for(
            tmp_path,
            compiler,
            model,
            profile={
                "type": "workspace-write",
                "writable_roots": [],
                "network_access": True,
                "exclude_tmpdir_env_var": True,
                "exclude_slash_tmp": True,
            },
            requirements=requirements,
        )
        try:
            events = [event async for event in runtime.stream("inspect effective restrictions")]
            assert isinstance(events[-1], TurnCompleted)
            text = fragments(model.requests[0], "permissions.instructions")[0].content
            assert "`sandbox_mode` is `workspace-write`" in text
            assert "Network access is enabled" in text
            assert f"The writable root is `{tmp_path}`" in text
            assert "## Denied filesystem reads" in text and str(blocked) in text
            assert "Do not request escalation or additional permissions to read them" in text
            environment = fragments(model.requests[0], "environments.environment_context")
            assert len(environment) == 1 and environment[0].role == "user"
            root = ElementTree.fromstring(environment[0].content)
            entries = root.findall("filesystem/permission_profile/file_system/entry")
            denied = [entry for entry in entries if entry.attrib["access"] == "deny"]
            assert any(
                entry.findtext("path") == str(blocked)
                and entry.attrib.get("escalatable") == "false"
                for entry in denied
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_granular_context_describes_categories_without_unavailable_tools(tmp_path, compiler, mode):
    async def scenario():
        model = Model(mode)
        runtime = await runtime_for(
            tmp_path,
            compiler,
            model,
            {
                "granular": {
                    "sandbox_approval": False,
                    "rules": True,
                    "mcp_elicitations": False,
                }
            },
        )
        try:
            events = [event async for event in runtime.stream("inspect granular approval")]
            assert isinstance(events[-1], TurnCompleted)
            text = fragments(model.requests[0], "permissions.instructions")[0].content
            assert "Approval policy is `granular`" in text
            allowed, rejected = text.split(
                "These approval categories are automatically rejected", 1
            )
            assert "- `rules`" in allowed
            assert "- `sandbox_approval`" in rejected and "- `mcp_elicitations`" in rejected
            assert "# request_permissions Tool" not in text
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_cold_resume_of_saved_rule_does_not_repeat_permissions(tmp_path, compiler, mode):
    async def scenario():
        (tmp_path / "home").mkdir()
        model = Model(
            mode,
            [
                {
                    "cmd": "printf saved",
                    "sandbox_permissions": "require_escalated",
                    "prefix_rule": ["printf"],
                }
            ],
        )
        runtime = await runtime_for(tmp_path, compiler, model)

        async def approve(request):
            runtime.respond_execution_approval(
                request.request_id, "accept", execpolicy_amendment=["printf"]
            )

        runtime.set_execution_approval_handler(approve)
        try:
            events = [event async for event in runtime.stream("save before restarting")]
            assert isinstance(events[-1], TurnCompleted)
            thread_id = runtime.thread_id
            before = fragments(model.requests[-1], "permissions.instructions")
            notices = fragments(model.requests[-1], "permissions.approved_command_prefix_saved")
            assert len(before) == len(notices) == 1
        finally:
            await runtime.aclose()
        restored_model = Model(mode)
        restored = await runtime_for(tmp_path, compiler, restored_model, thread_id=thread_id)
        try:
            events = [event async for event in restored.stream("continue with saved rule")]
            assert isinstance(events[-1], TurnCompleted)
            assert fragments(restored_model.requests[0], "permissions.instructions") == before
            assert (
                fragments(restored_model.requests[0], "permissions.approved_command_prefix_saved")
                == notices
            )
        finally:
            await restored.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_manual_compaction_rebuilds_full_permissions_with_saved_prefix(tmp_path, compiler, mode):
    async def scenario():
        (tmp_path / "home").mkdir()

        class CompactingModel(Model):
            async def stream(self, request):
                if not request.tools:
                    yield ModelCompleted(
                        (
                            AssistantMessageItem(
                                "preserved task summary", request.items[-1].turn_id, new_step_id()
                            ),
                        )
                    )
                else:
                    async for event in super().stream(request):
                        yield event

        model = CompactingModel(
            mode,
            [
                {
                    "cmd": "printf saved",
                    "sandbox_permissions": "require_escalated",
                    "prefix_rule": ["printf"],
                }
            ],
        )
        runtime = await runtime_for(tmp_path, compiler, model)

        async def approve(request):
            runtime.respond_execution_approval(
                request.request_id, "accept", execpolicy_amendment=["printf"]
            )

        runtime.set_execution_approval_handler(approve)
        try:
            events = [event async for event in runtime.stream("save and continue after compacting")]
            assert isinstance(events[-1], TurnCompleted)
            prior = await runtime._repository.load_items(runtime.thread_id)
            events = [event async for event in runtime.compact()]
            assert isinstance(events[-1], TurnCompleted)
            assert any(isinstance(event, ContextCompacted) for event in events)
            events = [event async for event in runtime.stream("current task remains here")]
            assert isinstance(events[-1], TurnCompleted)
            items = fragments(model.requests[-1], "permissions.instructions")
            assert len(items) == 1 and '["printf"]' in items[0].content
            assert not fragments(model.requests[-1], "permissions.approved_command_prefix_saved")
            environment = fragments(model.requests[-1], "environments.environment_context")
            assert len(environment) == 1
            assert ElementTree.fromstring(environment[0].content).find("filesystem") is not None
            assert json.loads(environment[0].snapshot_state)["version"] == 2
            assert model.requests[-1].items[-1].content == "current task remains here"
            stored = await runtime._repository.load_items(runtime.thread_id)
            assert stored[: len(prior)] == prior
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_failed_save_does_not_notify_model_that_rule_was_saved(tmp_path, compiler, mode):
    async def scenario():
        (tmp_path / "home").mkdir()
        model = Model(
            mode,
            [
                {
                    "cmd": "printf approved",
                    "sandbox_permissions": "require_escalated",
                    "prefix_rule": ["printf"],
                }
            ],
        )
        runtime = await runtime_for(tmp_path, compiler, model)

        async def approve(request):
            runtime.respond_execution_approval(
                request.request_id, "accept", execpolicy_amendment=[]
            )

        runtime.set_execution_approval_handler(approve)
        try:
            events = [event async for event in runtime.stream("approve now, fail to save")]
            assert isinstance(events[-1], TurnCompleted)
            assert any(isinstance(event, WarningEvent) for event in events)
            assert len(model.requests) == 2
            assert len(fragments(model.requests[-1], "permissions.instructions")) == 1
            assert not fragments(model.requests[-1], "permissions.approved_command_prefix_saved")
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_removed_rule_after_restart_refreshes_full_permissions(tmp_path, compiler, mode):
    async def scenario():
        rules = tmp_path / "home/rules"
        rules.mkdir(parents=True)
        policy = rules / "default.rules"
        policy.write_text('prefix_rule(pattern=["printf"], decision="allow")\n')
        model = Model(mode)
        runtime = await runtime_for(tmp_path, compiler, model)
        try:
            events = [event async for event in runtime.stream("inspect saved prefix")]
            assert isinstance(events[-1], TurnCompleted)
            thread_id = runtime.thread_id
            before = fragments(model.requests[0], "permissions.instructions")
            assert len(before) == 1 and '["printf"]' in before[0].content
        finally:
            await runtime.aclose()
        policy.write_text("")
        next_model = Model(mode)
        restored = await runtime_for(tmp_path, compiler, next_model, thread_id=thread_id)
        try:
            events = [event async for event in restored.stream("inspect changed policy")]
            assert isinstance(events[-1], TurnCompleted)
            current = fragments(next_model.requests[0], "permissions.instructions")
            assert len(current) == 2 and current[0] == before[0]
            assert '["printf"]' not in current[1].content
            assert current[1].content.startswith("<permissions instructions>")
            assert "no longer apply" not in current[1].content
            assert not fragments(
                next_model.requests[0], "permissions.approved_command_prefix_saved"
            )
        finally:
            await restored.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("include", [False, True])
def test_switching_permission_context_mode_keeps_retirement_out_of_model_messages(
    tmp_path, compiler, mode, include
):
    async def scenario():
        model = Model(mode)
        runtime = await runtime_for(tmp_path, compiler, model, include=not include)
        try:
            events = [event async for event in runtime.stream("initial context mode")]
            assert isinstance(events[-1], TurnCompleted)
            thread_id = runtime.thread_id
        finally:
            await runtime.aclose()
        model = Model(mode)
        runtime = await runtime_for(tmp_path, compiler, model, thread_id=thread_id, include=include)
        try:
            events = [event async for event in runtime.stream("new context mode")]
            assert isinstance(events[-1], TurnCompleted)
            items = [item for item in model.requests[0].items if isinstance(item, ContextItem)]
            assert all(item.content for item in items)
            assert len(fragments(model.requests[0], "permissions.instructions")) == 1
            assert not any(
                "no longer apply" in item.content
                for item in items
                if item.key in {"permissions", "approved_command_prefixes"}
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
