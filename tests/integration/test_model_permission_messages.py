"""Catalog permission text must survive parsing and reach actual model requests."""

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
from corki.config.model_context import parse_model_contexts
from corki.config.permissions import ExecutionPermissions
from corki.context.permissions import PermissionContext
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.items import ContextItem, ToolResultItem
from corki.protocol.permission_messages import ModelPermissionMessages
from corki.protocol.tools import ToolResult, ToolSpec
from corki.tools import ToolRegistry


class Model:
    def __init__(self):
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        yield ModelCompleted(())

    async def aclose(self):
        pass


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("variant", ["custom", "empty", "fallback"])
def test_catalog_messages_are_used_by_runtime_context(tmp_path, mode, native, variant):
    async def scenario():
        if mode != "direct" and not CodeModeService.available():
            pytest.skip("install corki[code-mode]")
        compiler = os.environ.get("CORKI_TEST_SANDBOX_COMPILER")
        if native and (not compiler or sys.platform != "darwin"):
            pytest.skip("requires explicit native compiler and macOS")
        sandbox = {
            "custom": "catalog network={{ network_access }} compact={{network_access}} #{literal}",
            "empty": "",
            "fallback": None,
        }[variant]
        approval = {"custom": "catalog never ask", "empty": "", "fallback": None}[variant]
        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            tool_mode=mode,
            model="fixture",
            model_contexts=parse_model_contexts(
                {
                    "fixture": {
                        "model_messages": {
                            "permissions": {
                                "read_only" if native else "danger_full_access": sandbox
                            },
                            "approvals": {"never": approval},
                        }
                    }
                }
            ),
            execution_permissions=ExecutionPermissions(
                Path(compiler), tmp_path, '{"type":"read-only"}'
            )
            if native
            else None,
        )
        model = Model()
        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            model=model,
            home_path=tmp_path / "home",
            database_path=tmp_path / "state.db",
        )
        try:
            events = [
                event async for event in runtime.stream("inspect model-specific instructions")
            ]
            assert isinstance(events[-1], TurnCompleted)
            items = [
                item
                for item in model.requests[0].items
                if isinstance(item, ContextItem) and item.content_kind == "permissions.instructions"
            ]
            assert len(items) == 1
            text = items[0].content.strip()
            if variant == "empty":
                assert text == "<permissions instructions>\n</permissions instructions>"
            elif variant == "custom":
                network = "restricted" if native else "enabled"
                assert (
                    f"catalog network={network} compact={{{{network_access}}}} #{{literal}}" in text
                )
                assert "catalog never ask" in text
                assert "Filesystem sandboxing" not in text
            else:
                assert "Filesystem sandboxing" in text
                assert "Approval policy is currently never" in text
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "   ",
        "\n",
        "network={{ network_access }} compact={{network_access}}",
        "unterminated {{",
        "#{outer}",
        "权限说明",
    ],
)
def test_legacy_rendering_matches_native_disabled_never_bytes(tmp_path, text):
    async def scenario():
        compiler = os.environ.get("CORKI_TEST_SANDBOX_COMPILER")
        if not compiler:
            pytest.skip("requires native compiler")
        messages = ModelPermissionMessages(danger_full_access=text, never=text)
        native = await PermissionContext().snapshot(
            ExecutionPermissions(Path(compiler), tmp_path, '{"type":"disabled"}'),
            tmp_path,
            honor_allow_rules=True,
            messages=messages,
        )
        legacy = await PermissionContext().snapshot(
            None, tmp_path, honor_allow_rules=True, messages=messages
        )
        assert legacy.text == native.text
        assert legacy.without_prefixes == native.without_prefixes

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("native", [False, True])
def test_bundled_auto_review_model_suppresses_default_permission_sections(tmp_path, mode, native):
    async def scenario():
        if mode != "direct" and not CodeModeService.available():
            pytest.skip("install corki[code-mode]")
        compiler = os.environ.get("CORKI_TEST_SANDBOX_COMPILER")
        if native and (not compiler or sys.platform != "darwin"):
            pytest.skip("requires native compiler")
        model = Model()
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                tool_mode=mode,
                model="codex-auto-review",
                execution_permissions=ExecutionPermissions(
                    Path(compiler), tmp_path, '{"type":"read-only"}'
                )
                if native
                else None,
            ),
            model=model,
            database_path=tmp_path / "state.db",
            home_path=tmp_path / "home",
        )
        try:
            events = [event async for event in runtime.stream("inspect bundled messages")]
            assert isinstance(events[-1], TurnCompleted)
            items = [
                item
                for item in model.requests[0].items
                if isinstance(item, ContextItem) and item.content_kind == "permissions.instructions"
            ]
            assert (
                len(items) == 1
                and items[0].content.strip()
                == "<permissions instructions>\n</permissions instructions>"
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("native", [False, True])
def test_step_switch_keeps_turn_messages_but_next_turn_uses_new_model(tmp_path, mode, native):
    from test_thread_settings_update import Model as StepModel
    from test_thread_settings_update import make_runtime, settings

    async def scenario():
        if mode != "direct" and not CodeModeService.available():
            pytest.skip("install corki[code-mode]")
        compiler = os.environ.get("CORKI_TEST_SANDBOX_COMPILER")
        if native and (not compiler or sys.platform != "darwin"):
            pytest.skip("requires native compiler")
        entered, release = asyncio.Event(), asyncio.Event()

        class Hold:
            spec = ToolSpec("hold", "hold until settings are published", {"type": "object"})

            async def execute(self, call, context):
                entered.set()
                await release.wait()
                return ToolResult(call.id, call.name, "done")

        configured = settings(tmp_path, tool_mode=mode, step_model_switching=True)
        configured = replace(
            configured,
            model_contexts=tuple(
                replace(
                    info,
                    permission_messages=ModelPermissionMessages(never=info.model + "-permission"),
                )
                for info in configured.model_contexts
            ),
            execution_permissions=ExecutionPermissions(
                Path(compiler), tmp_path, '{"type":"read-only"}'
            )
            if native
            else None,
        )
        registry = ToolRegistry()
        registry.register(Hold())
        model = StepModel(calls=True, code_mode=mode != "direct")
        runtime = await make_runtime(tmp_path, model, configured=configured, registry=registry)

        async def consume():
            return [event async for event in runtime.stream("switch model during a turn")]

        work = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(entered.wait(), 10)
            result = await runtime.update_turn_settings(runtime._active_run.turn_id, model="small")
            assert result.status == "applied"
            release.set()
            assert isinstance((await work)[-1], TurnCompleted)
            assert [request.model for request in model.requests] == ["large", "small"]

            def permission_items(request):
                return [
                    item
                    for item in request.items
                    if isinstance(item, ContextItem)
                    and item.content_kind == "permissions.instructions"
                ]

            assert len(permission_items(model.requests[1])) == 1
            assert "large-permission" in permission_items(model.requests[1])[-1].content
            await runtime.update_thread_settings(model="small")
            events = [event async for event in runtime.stream("new turn")]
            assert isinstance(events[-1], TurnCompleted)
            assert "small-permission" in permission_items(model.requests[-1])[-1].content
            thread_id = runtime.thread_id
            await runtime.aclose()
            restored_model = Model()
            restored = await make_runtime(
                tmp_path,
                restored_model,
                configured=replace(configured, model="small"),
                thread=thread_id,
            )
            try:
                events = [event async for event in restored.stream("cold continuation")]
                assert isinstance(events[-1], TurnCompleted)
                assert permission_items(restored_model.requests[0]) == permission_items(
                    model.requests[-1]
                )
            finally:
                await restored.aclose()
        finally:
            release.set()
            await asyncio.gather(work, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("approval_text", ["", "custom approval guidance"])
def test_approval_override_does_not_suppress_saved_prefix_delta(tmp_path, mode, approval_text):
    from test_execution_permission_context import Model as CommandModel

    async def scenario():
        compiler = os.environ.get("CORKI_TEST_SANDBOX_COMPILER")
        if not compiler or sys.platform != "darwin":
            pytest.skip("requires native compiler and macOS")
        if mode != "direct" and not CodeModeService.available():
            pytest.skip("install corki[code-mode]")
        rules = tmp_path / "home/rules"
        rules.mkdir(parents=True)
        (rules / "default.rules").write_text('prefix_rule(pattern=["echo"], decision="allow")\n')
        model = CommandModel(
            mode,
            [
                {
                    "cmd": "printf approved",
                    "sandbox_permissions": "require_escalated",
                    "prefix_rule": ["printf"],
                }
            ],
        )
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                tool_mode=mode,
                model="fixture",
                model_contexts=parse_model_contexts(
                    {"fixture": {"model_messages": {"approvals": {"on_request": approval_text}}}}
                ),
                execution_permissions=ExecutionPermissions(
                    Path(compiler),
                    tmp_path,
                    '{"type":"read-only"}',
                    approval_policy_json=json.dumps("on-request"),
                ),
            ),
            model=model,
            home_path=tmp_path / "home",
            database_path=tmp_path / "state.db",
        )

        async def approve(request):
            runtime.respond_execution_approval(
                request.request_id, "accept", execpolicy_amendment=["printf"]
            )

        runtime.set_execution_approval_handler(approve)
        try:
            events = [event async for event in runtime.stream("approve a rule with custom text")]
            assert isinstance(events[-1], TurnCompleted)
            initial = [
                item
                for item in model.requests[0].items
                if isinstance(item, ContextItem) and item.content_kind == "permissions.instructions"
            ]
            assert len(initial) == 1 and "Approved command prefixes" not in initial[0].content
            notices = [
                item
                for item in model.requests[-1].items
                if isinstance(item, ContextItem)
                and item.content_kind == "permissions.approved_command_prefix_saved"
            ]
            assert (
                len(notices) == 1
                and notices[0].content == 'Approved command prefix saved:\n- ["printf"]'
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("native", [False, True])
def test_empty_model_messages_do_not_disable_execution_gate(tmp_path, mode, native):
    from test_execution_permission_context import Model as CommandModel

    async def scenario():
        if mode != "direct" and not CodeModeService.available():
            pytest.skip("install corki[code-mode]")
        compiler = os.environ.get("CORKI_TEST_SANDBOX_COMPILER")
        if native and (not compiler or sys.platform != "darwin"):
            pytest.skip("requires native compiler and macOS")
        target = tmp_path / "must-not-execute"
        model = CommandModel(
            mode,
            [
                {
                    "cmd": "touch " + shlex.quote(str(target)),
                    "sandbox_permissions": "require_escalated",
                }
            ],
        )
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                tool_mode=mode,
                model="codex-auto-review",
                execution_permissions=ExecutionPermissions(
                    Path(compiler), tmp_path, '{"type":"read-only"}'
                )
                if native
                else None,
            ),
            model=model,
            home_path=tmp_path / "home",
            database_path=tmp_path / "state.db",
        )
        try:
            events = [
                event async for event in runtime.stream("empty instructions do not grant authority")
            ]
            assert isinstance(events[-1], TurnCompleted)
            assert not target.exists() and len(model.requests) == 2
            results = [
                item for item in model.requests[-1].items if isinstance(item, ToolResultItem)
            ]
            assert results and results[-1].is_error
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
