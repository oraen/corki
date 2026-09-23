"""A pending checkpoint retains its base prefix across host reconfiguration."""

import asyncio
from dataclasses import replace

import pytest
from test_thread_settings_update import Model, make_runtime, settings

from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted, TurnFailed
from corki.protocol.ids import new_turn_id
from corki.protocol.items import AssistantMessageItem, CompactionItem, UserMessageItem, new_step_id
from corki.protocol.tools import ToolResult, ToolSpec
from corki.sessions import TurnRecord, TurnStatus
from corki.tools import ToolRegistry


@pytest.mark.parametrize("ledger_base", [None, "PREPARED BASE", "", "CONFLICT"])
def test_resume_validates_base_against_ledger_before_sampling(tmp_path, ledger_base):
    async def scenario():
        class Sink:
            async def emit(self, event):
                pass

        source = await make_runtime(
            tmp_path,
            Model(),
            configured=replace(settings(tmp_path), base_instructions="PREPARED BASE"),
        )
        try:
            await source._ensure_ready()
            turn = new_turn_id()
            user = UserMessageItem("prepared input", turn)
            state = _initial_state(source.thread_id, turn, source._settings, user)
            await source._repository.save_turn(
                TurnRecord(
                    turn,
                    source.thread_id,
                    TurnStatus.RUNNING,
                    user.content,
                    model_settings=state["turn_model_settings"],
                    base_instructions=ledger_base,
                )
            )
            await source._compiled.ainvoke(
                state,
                config=source._graph_config(turn),
                context=GraphRunContext(events=Sink()),
                interrupt_before=["call_model"],
            )
            thread = source.thread_id
        finally:
            await source.aclose()
        model = Model()
        cold = await make_runtime(tmp_path, model, thread=thread)
        try:
            if ledger_base in (None, "PREPARED BASE"):
                assert isinstance([e async for e in cold.resume_pending()][-1], TurnCompleted)
                assert len(model.requests) == 1
                assert model.requests[0].instructions == "PREPARED BASE"
            else:
                with pytest.raises(ValueError, match="base instructions.*checkpoint"):
                    _ = [e async for e in cold.resume_pending()]
                assert not model.requests
                pending = await cold._repository.latest_running_turn(thread)
                assert pending.id == turn and pending.base_instructions == ledger_base
        finally:
            await cold.aclose()

    asyncio.run(scenario())


def test_legacy_compaction_without_base_replays_installed_summary_under_new_override(tmp_path):
    async def scenario():
        source = await make_runtime(
            tmp_path,
            Model(),
            configured=replace(settings(tmp_path), base_instructions="ADMITTED BASE"),
        )
        try:
            await source._ensure_ready()
            thread, turn = source.thread_id, new_turn_id()
            await source._repository.save_turn(
                TurnRecord(turn, thread, TurnStatus.RUNNING, "", operation="compact")
            )
            marker = CompactionItem("SAVED SUMMARY", None, turn)
            await source._repository.append_items(thread, (marker,))
        finally:
            await source.aclose()
        model = Model()
        cold = await make_runtime(
            tmp_path,
            model,
            thread=thread,
            configured=replace(settings(tmp_path), base_instructions="FUTURE BASE"),
        )
        try:
            assert isinstance([e async for e in cold.resume_pending()][-1], TurnCompleted)
            assert not model.requests
            assert await cold._repository.load_items(thread) == (marker,)
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("large_history", [False, True])
def test_legacy_turn_without_base_replays_committed_model_under_new_override(
    tmp_path, large_history
):
    async def scenario():
        source = await make_runtime(
            tmp_path,
            Model(),
            configured=replace(settings(tmp_path), base_instructions="ADMITTED BASE"),
        )
        try:
            await source._ensure_ready()
            thread, turn = source.thread_id, new_turn_id()
            await source._repository.save_turn(
                TurnRecord(turn, thread, TurnStatus.RUNNING, "input")
            )
            if large_history:
                await source._repository.append_items(
                    thread, (UserMessageItem("x" * 20000, new_turn_id()),)
                )
            await source._repository.append_items(thread, (UserMessageItem("input", turn),))
            await source._repository.commit_model_step(
                thread,
                turn,
                0,
                ModelCompleted((AssistantMessageItem("SAVED ANSWER", turn, new_step_id()),)),
            )
        finally:
            await source.aclose()
        model = Model()
        cold = await make_runtime(
            tmp_path,
            model,
            thread=thread,
            configured=replace(
                settings(tmp_path),
                base_instructions="FUTURE BASE",
                context_window_tokens=8000,
                auto_compact_tokens=3000,
            ),
        )
        try:
            events = [e async for e in cold.resume_pending()]
            assert isinstance(events[-1], TurnFailed if large_history else TurnCompleted), events[
                -1
            ]
            if large_history:
                assert "missing admitted Turn base instructions" in events[-1].error
                assert not any(
                    isinstance(item, CompactionItem)
                    for item in await cold._repository.load_items(thread)
                )
            assert not model.requests
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["normal", "compact"])
@pytest.mark.parametrize("admitted_base", ["ADMITTED BASE", ""])
@pytest.mark.parametrize("legacy_missing_base", [False, True])
def test_ledger_only_turn_retains_base_before_first_checkpoint(
    tmp_path, monkeypatch, operation, admitted_base, legacy_missing_base
):
    async def scenario():
        model = Model()
        source = await make_runtime(
            tmp_path,
            model,
            configured=replace(settings(tmp_path), base_instructions=admitted_base),
        )
        await source._ensure_ready()
        save = source._repository.save_turn

        async def save_running_only(turn):
            if turn.status is not TurnStatus.RUNNING:
                raise OSError("fixture terminal storage unavailable")
            await save(turn)

        async def crash(*args, **kwargs):
            raise OSError("fixture crash before checkpoint")

        monkeypatch.setattr(source._repository, "save_turn", save_running_only)
        monkeypatch.setattr(source._repository, "retry_turn_terminal", save_running_only)
        monkeypatch.setattr(source._compiled, "ainvoke", crash)
        try:
            stream = source.stream("admitted once") if operation == "normal" else source.compact()
            assert isinstance([e async for e in stream][-1], TurnFailed)
            pending = await source._repository.latest_running_turn(source.thread_id)
            assert pending is not None and pending.operation == operation
            assert pending.base_instructions == admitted_base
            assert not model.requests
            thread = source.thread_id
            if legacy_missing_base:
                with source._repository._connect() as connection:
                    connection.execute(
                        "UPDATE turns SET base_instructions=NULL WHERE id=?",
                        (str(pending.id),),
                    )
        finally:
            # Preserve the crash boundary while still releasing fixture resources.
            source._pending_terminals.clear()
            await source.aclose()
        cold_model = Model()
        cold = await make_runtime(
            tmp_path,
            cold_model,
            thread=thread,
            configured=replace(settings(tmp_path), base_instructions="FUTURE BASE"),
        )
        try:
            if legacy_missing_base:
                events = [e async for e in cold.resume_pending()]
                assert isinstance(events[-1], TurnFailed)
                assert "missing admitted Turn base instructions" in events[-1].error
                assert not cold_model.requests
                assert await cold._repository.latest_running_turn(thread) is None
                return
            assert isinstance([e async for e in cold.resume_pending()][-1], TurnCompleted)
            assert len(cold_model.requests) == 1
            assert cold_model.requests[0].instructions == admitted_base
            assert isinstance([e async for e in cold.stream("future")][-1], TurnCompleted)
            assert cold_model.requests[-1].instructions == "FUTURE BASE"
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("after_tools", [False, True])
@pytest.mark.parametrize("admitted_base", ["ADMITTED BASE", ""])
def test_pending_turn_base_is_not_replaced_by_new_host_override(
    tmp_path, after_tools, admitted_base
):
    async def scenario():
        executions = []

        class Sink:
            async def emit(self, event):
                pass

        class Tool:
            spec = ToolSpec("hold", "fixture", {"type": "object"})

            async def execute(self, call, context):
                executions.append(call.id)
                return ToolResult(call.id, call.name, "committed")

        registry = ToolRegistry()
        registry.register(Tool())
        original_model = Model(calls=after_tools)
        source = await make_runtime(
            tmp_path,
            original_model,
            registry=registry,
            configured=replace(settings(tmp_path), base_instructions=admitted_base),
        )
        try:
            await source._ensure_ready()
            turn = new_turn_id()
            user = UserMessageItem("admitted input", turn)
            state = _initial_state(source.thread_id, turn, source._settings, user)
            await source._repository.save_turn(
                TurnRecord(
                    turn,
                    source.thread_id,
                    TurnStatus.RUNNING,
                    user.content,
                    model_settings=state["turn_model_settings"],
                )
            )
            await source._compiled.ainvoke(
                state,
                config=source._graph_config(turn),
                context=GraphRunContext(events=Sink()),
                **(
                    {"interrupt_after": ["execute_tools"]}
                    if after_tools
                    else {"interrupt_before": ["call_model"]}
                ),
            )
            assert len(executions) == int(after_tools)
            thread = source.thread_id
        finally:
            await source.aclose()
        model = Model()
        registry = ToolRegistry()
        registry.register(Tool())
        cold = await make_runtime(
            tmp_path,
            model,
            registry=registry,
            thread=thread,
            configured=replace(settings(tmp_path), base_instructions="FUTURE BASE"),
        )
        try:
            assert isinstance([e async for e in cold.resume_pending()][-1], TurnCompleted)
            assert len(model.requests) == 1
            assert model.requests[0].instructions == admitted_base
            assert len(executions) == int(after_tools)
            assert isinstance([e async for e in cold.stream("future")][-1], TurnCompleted)
            assert model.requests[-1].instructions == "FUTURE BASE"
        finally:
            await cold.aclose()

    asyncio.run(scenario())
