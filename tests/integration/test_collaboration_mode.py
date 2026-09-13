"""Host mode selection reaches ordinary model context and tool execution."""

import asyncio
import sqlite3
from dataclasses import replace

import pytest
from test_thread_settings_update import Model as SettingsModel
from test_thread_settings_update import make_runtime, settings

from corki.code_mode.service import CodeModeService
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.models import ModelCompleted
from corki.protocol.collaboration import CollaborationMode
from corki.protocol.events import PlanUpdated, TurnCompleted
from corki.protocol.ids import new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.settings import ModelSettingsSnapshot
from corki.protocol.tools import ToolCall
from corki.sessions import TurnRecord, TurnStatus
from corki.storage.sqlite import SQLiteSessionRepository


@pytest.mark.parametrize("tool_mode", ["direct", "code_mode_only"])
def test_mode_changes_future_turn_context_and_plan_guard(tmp_path, tool_mode):
    if tool_mode != "direct" and not CodeModeService.available():
        pytest.skip("install corki[code-mode]")

    async def scenario():
        class Model:
            requests = []

            async def stream(self, request):
                self.requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(self.requests) % 2:
                    call = ToolCall(f"plan-{len(self.requests)}", "update_plan", {"plan": []})
                    if tool_mode != "direct":
                        call = ToolCall(
                            call.id,
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments="await tools.update_plan({plan: []});",
                        )
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                call,
                                turn,
                                step,
                            ),
                        )
                    )
                else:
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        model = Model()
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                tool_mode=tool_mode,
            ),
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
            model=model,
        )
        try:
            snapshot = await runtime.update_thread_settings(
                model="ignored",
                reasoning_effort="high",
                collaboration_mode=CollaborationMode("plan", "gpt-5", "low"),
            )
            assert (snapshot.model, snapshot.reasoning_effort) == ("gpt-5", "low")
            assert ModelSettingsSnapshot.from_payload(snapshot.to_payload()) == snapshot
            events = [e async for e in runtime.stream("plan the work")]
            assert isinstance(events[-1], TurnCompleted)
            assert not any(isinstance(e, PlanUpdated) for e in events)
            result = next(
                i for i in reversed(model.requests[1].items) if isinstance(i, ToolResultItem)
            )
            assert "not allowed in Plan mode" in result.content
            if tool_mode == "direct":
                assert result.is_error
            contexts = [i for i in model.requests[0].items if isinstance(i, ContextItem)]
            assert any("Plan Mode (Conversational)" in i.content for i in contexts)
            assert not any("Collaboration Mode: Default" in i.content for i in contexts)
            saved = await runtime._repository.load_thread_model_settings(runtime.thread_id)
            assert saved.collaboration_mode == "plan"
            await runtime.update_thread_settings(reasoning_effort=None)
            assert runtime.thread_settings.collaboration_mode == "plan"
            assert runtime.thread_settings.reasoning_effort is None
            await runtime.update_thread_settings(
                collaboration_mode=CollaborationMode("default", "gpt-5")
            )
            events = [e async for e in runtime.stream("implement it")]
            assert isinstance(events[-1], TurnCompleted)
            assert sum(isinstance(e, PlanUpdated) for e in events) == 1
            result = next(
                i for i in reversed(model.requests[3].items) if isinstance(i, ToolResultItem)
            )
            assert not result.is_error
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_custom_mode_instructions_are_replaced_and_removed_in_real_context(tmp_path):
    async def scenario():
        model = SettingsModel()
        runtime = make_runtime(tmp_path, model)
        try:
            for instructions in ("Unique planning instructions", "", None):
                await runtime.update_thread_settings(
                    collaboration_mode=CollaborationMode(
                        "plan",
                        "large",
                        developer_instructions=instructions,
                    )
                )
                assert isinstance([e async for e in runtime.stream("next")][-1], TurnCompleted)
                # World-state changes append a replacement/revocation; original
                # history remains available and is not destructively rewritten.
                context = next(
                    i.content
                    for i in reversed(model.requests[-1].items)
                    if isinstance(i, ContextItem) and i.key == "mode.plan"
                )
                assert ("Unique planning instructions" in context) == bool(instructions)
                assert ("Plan Mode (Conversational)" in context) == (instructions is None)
                if instructions == "":
                    assert context == "<collaboration_mode></collaboration_mode>"
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_mode_snapshot_tracks_model_and_emits_one_section_per_transition(tmp_path):
    async def scenario():
        model = SettingsModel()
        runtime = make_runtime(tmp_path, model)
        try:
            for mode, selected_model, instructions, expected in (
                ("default", "large", "same instructions", 1),
                ("plan", "large", "same instructions", 1),
                ("plan", "small", "same instructions", 1),
                ("default", "small", "", 1),
                ("plan", "large", "", 0),
            ):
                before = await runtime._repository.load_items(runtime.thread_id)
                await runtime.update_thread_settings(
                    collaboration_mode=CollaborationMode(
                        mode, selected_model, developer_instructions=instructions
                    )
                )
                for _ in range(2):
                    assert isinstance([e async for e in runtime.stream("next")][-1], TurnCompleted)
                stored = await runtime._repository.load_items(runtime.thread_id)
                assert stored[: len(before)] == before
                updates = [
                    i
                    for i in stored[len(before) :]
                    if isinstance(i, ContextItem) and i.key in {"mode.default", "mode.plan"}
                ]
                assert len(updates) == expected
                if expected:
                    assert updates[0].content == (
                        f"<collaboration_mode>{instructions}</collaboration_mode>"
                    )
                    assert updates[0] in model.requests[-1].items
                thread = runtime.thread_id
                await runtime.aclose()
                runtime = make_runtime(tmp_path, model, thread=thread)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("instructions", ["old instructions", "new instructions", ""])
def test_legacy_mode_fragment_is_reconciled_once_on_cold_resume(tmp_path, instructions):
    from corki.protocol.messages import Message, MessageRole

    async def scenario():
        model = SettingsModel()
        runtime = make_runtime(tmp_path, model)
        await runtime._ensure_ready()
        thread = runtime.thread_id
        try:
            await runtime._repository.save_messages(
                thread,
                (
                    Message.create(
                        role=MessageRole.DEVELOPER,
                        content="<collaboration_mode>old instructions</collaboration_mode>",
                        turn_id=new_turn_id(),
                    ),
                ),
            )
            prefix = await runtime._repository.load_items(thread)
        finally:
            await runtime.aclose()
        runtime = make_runtime(tmp_path, model, thread=thread)
        try:
            await runtime.update_thread_settings(
                collaboration_mode=CollaborationMode(
                    "plan", "large", developer_instructions=instructions
                )
            )
            for _ in range(2):
                assert isinstance([e async for e in runtime.stream("next")][-1], TurnCompleted)
                await runtime.aclose()
                # A new host supplies future-Turn defaults explicitly; keep
                # those defaults fixed while testing history reconciliation.
                runtime = make_runtime(
                    tmp_path,
                    model,
                    thread=thread,
                    configured=settings(
                        tmp_path,
                        collaboration_mode="plan",
                        collaboration_instructions=instructions,
                    ),
                )
            stored = await runtime._repository.load_items(thread)
            assert stored[: len(prefix)] == prefix
            updates = [
                i
                for i in stored[len(prefix) :]
                if isinstance(i, ContextItem)
                and i.key in {"legacy.developer", "mode.default", "mode.plan"}
            ]
            assert len(updates) == 1, [
                (i.key, i.snapshot_state, i.snapshot_content, i.content) for i in updates
            ]
            assert updates[0].key == "mode.plan"
            assert updates[0].content == f"<collaboration_mode>{instructions}</collaboration_mode>"
            assert all(updates[0] in request.items for request in model.requests)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_legacy_mode_settings_migrate_and_snapshots_default_safely(tmp_path):
    async def scenario():
        runtime = make_runtime(tmp_path, SettingsModel())
        await runtime._ensure_ready()
        path, thread = runtime._repository.path, runtime.thread_id
        payload = runtime.thread_settings.to_payload()
        payload.pop("collaboration_mode")
        payload.pop("collaboration_instructions")
        assert ModelSettingsSnapshot.from_payload(payload).collaboration_mode == "default"
        await runtime.aclose()
        with sqlite3.connect(path) as connection:
            connection.execute("ALTER TABLE thread_model_settings DROP COLUMN collaboration_mode")
            connection.execute(
                "ALTER TABLE thread_model_settings DROP COLUMN collaboration_instructions"
            )
        repository = SQLiteSessionRepository(path)
        try:
            restored = await repository.load_thread_model_settings(thread)
            assert restored.collaboration_mode == "default"
            assert restored.collaboration_instructions is None
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_checkpoint_recovery_retains_mode_against_new_host_defaults(tmp_path):
    async def scenario():
        class Sink:
            async def emit(self, event):
                pass

        configured = settings(tmp_path, collaboration_mode="plan")
        runtime = make_runtime(tmp_path, SettingsModel(), configured=configured)
        await runtime._ensure_ready()
        turn = new_turn_id()
        user = UserMessageItem("prepared once", turn)
        state = _initial_state(runtime.thread_id, turn, configured, user)
        await runtime._repository.save_turn(
            TurnRecord(
                turn,
                runtime.thread_id,
                TurnStatus.RUNNING,
                user.content,
                model_settings=state["turn_model_settings"],
            )
        )
        try:
            await runtime._compiled.ainvoke(
                state,
                config=runtime._graph_config(turn),
                context=GraphRunContext(events=Sink()),
                interrupt_before=["call_model"],
            )
            await runtime.update_thread_settings(
                collaboration_mode=CollaborationMode("default", "large")
            )
        finally:
            await runtime.aclose()
        model = SettingsModel()
        cold = make_runtime(
            tmp_path,
            model,
            configured=replace(configured, collaboration_mode="default"),
            thread=runtime.thread_id,
        )
        try:
            events = [e async for e in cold.resume_pending()]
            assert isinstance(events[-1], TurnCompleted)
            assert cold.thread_settings.collaboration_mode == "default"
            assert any(
                isinstance(i, ContextItem) and "Plan Mode (Conversational)" in i.content
                for i in model.requests[0].items
            )
            assert not any(
                isinstance(i, ContextItem) and "Collaboration Mode: Default" in i.content
                for i in model.requests[0].items
            )
        finally:
            await cold.aclose()

    asyncio.run(scenario())
