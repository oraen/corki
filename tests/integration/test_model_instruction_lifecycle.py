"""Local catalog instructions retain the session prefix across model changes."""

import asyncio
import threading
from dataclasses import replace

import pytest
from test_thread_settings_update import Model, make_runtime, settings

from corki.config.model_context import parse_model_contexts
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.models.types import ModelUsage
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import ContextCompacted, TurnCompleted
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize("compacted", [False, True])
@pytest.mark.parametrize("next_model", ["small", "large"])
def test_legacy_model_marker_recovers_without_instruction_section(tmp_path, compacted, next_model):
    async def scenario():
        configured = replace(
            settings(tmp_path),
            model_contexts=(
                ModelContextInfo("large", 200_000, base_instructions="INITIAL RULES"),
                ModelContextInfo("small", 200_000, base_instructions="SMALL RULES"),
            ),
        )
        source = make_runtime(tmp_path, Model(), configured=configured)
        try:
            assert isinstance([e async for e in source.stream("initial")][-1], TurnCompleted)
            await source.update_thread_settings(model="small")
            assert isinstance([e async for e in source.stream("old small turn")][-1], TurnCompleted)
            if compacted:
                assert isinstance([e async for e in source.compact()][-1], TurnCompleted)
            # Emulate the pre-model-instructions schema: the accepted-model
            # marker exists, but the later instruction section does not.
            with source._repository._connect() as connection:
                connection.execute(
                    "DELETE FROM conversation_items WHERE thread_id=? "
                    "AND json_extract(payload_json, '$.key')='model.instructions'",
                    (str(source.thread_id),),
                )
            prefix = await source._repository.load_items(source.thread_id)
            assert any(
                isinstance(i, ContextItem) and i.key == "harness.previous_model" for i in prefix
            )
            thread = source.thread_id
        finally:
            await source.aclose()
        model = Model()
        cold = make_runtime(
            tmp_path, model, configured=replace(configured, model=next_model), thread=thread
        )
        try:
            assert isinstance([e async for e in cold.stream("cold")][-1], TurnCompleted)
            # Only count newly appended instructions, not legacy model history.
            stored = await cold._repository.load_items(thread)
            assert stored[: len(prefix)] == prefix
            updates = [
                i
                for i in stored[len(prefix) :]
                if isinstance(i, ContextItem) and i.key == "model.instructions" and i.content
            ]
            assert len(updates) == int(next_model != "small")
            if updates:
                assert "INITIAL RULES" in updates[0].content
            assert model.requests[-1].instructions == "INITIAL RULES"
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("next_model", ["large", "small"])
def test_mid_turn_compaction_keeps_model_identity_and_tool_commit(tmp_path, next_model):
    async def scenario():
        executions = []

        class Tool:
            spec = ToolSpec("inspect", "Inspect fixture", {"type": "object"})

            async def execute(self, call, context):
                executions.append(call.id)
                return ToolResult(call.id, call.name, "COMMITTED OBSERVATION")

        class UsageModel(Model):
            async def stream(self, request):
                self.requests.append(request)
                index = len(self.requests)
                turn, step = request.items[-1].turn_id, new_step_id()
                item = (
                    ToolCallItem(ToolCall("inspect-once", "inspect", {}), turn, step)
                    if index == 2
                    else AssistantMessageItem("summary" if index == 3 else "done", turn, step)
                )
                yield ModelCompleted((item,), ModelUsage(180_000 if index == 2 else 100, 10))

        configured = replace(
            settings(tmp_path),
            auto_compact_tokens=100_000,
            model_contexts=(
                ModelContextInfo("large", 200_000, base_instructions="INITIAL RULES"),
                ModelContextInfo("small", 200_000, base_instructions="SMALL RULES"),
            ),
        )
        model = UsageModel()
        registry = ToolRegistry()
        registry.register(Tool())
        runtime = make_runtime(tmp_path, model, configured=configured, registry=registry)
        try:
            assert isinstance([e async for e in runtime.stream("initial")][-1], TurnCompleted)
            await runtime.update_thread_settings(model=next_model)
            events = [e async for e in runtime.stream("KEEP CURRENT INPUT")]
            assert isinstance(events[-1], TurnCompleted)
            assert any(isinstance(e, ContextCompacted) for e in events)
            assert len(model.requests) == 4
            assert executions == ["inspect-once"]
            assert all(r.instructions == "INITIAL RULES" for r in model.requests)
            summary = model.requests[2]
            assert summary.tools == ()
            calls = [i.call.id for i in summary.items if isinstance(i, ToolCallItem)]
            results = [i for i in summary.items if isinstance(i, ToolResultItem)]
            assert calls == ["inspect-once"]
            assert len(results) == 1 and results[0].content == "COMMITTED OBSERVATION"
            final = model.requests[-1]
            assert not any(
                isinstance(i, ContextItem) and i.content_kind == "model_switch.instructions"
                for i in final.items
            )
            assert any(
                isinstance(i, UserMessageItem) and i.content == "KEEP CURRENT INPUT"
                for i in final.items
            )
            stored = await runtime._repository.load_items(runtime.thread_id)
            assert len([i for i in stored if isinstance(i, ToolCallItem)]) == 1
            assert len([i for i in stored if isinstance(i, ToolResultItem)]) == 1
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("next_model", ["small", "large"])
def test_auto_compaction_reinjects_model_diff_not_raw_catalog(tmp_path, next_model):
    async def scenario():
        class UsageModel(Model):
            async def stream(self, request):
                self.requests.append(request)
                count = (100, 180_000, 20, 100)[len(self.requests) - 1]
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),),
                    ModelUsage(count, 10),
                )

        configured = replace(
            settings(tmp_path),
            model_contexts=(
                ModelContextInfo("large", 200_000, base_instructions="INITIAL RULES"),
                ModelContextInfo("small", 200_000, base_instructions="SMALL RULES"),
            ),
            auto_compact_tokens=100_000,
        )
        model = UsageModel()
        runtime = make_runtime(tmp_path, model, configured=configured)
        try:
            assert isinstance([e async for e in runtime.stream("initial")][-1], TurnCompleted)
            await runtime.update_thread_settings(model="small")
            assert isinstance([e async for e in runtime.stream("small")][-1], TurnCompleted)
            await runtime.update_thread_settings(model=next_model)
            events = [e async for e in runtime.stream("auto")]
            assert isinstance(events[-1], TurnCompleted)
            assert any(isinstance(e, ContextCompacted) for e in events)
            assert len(model.requests) == 4
            assert all(r.instructions == "INITIAL RULES" for r in model.requests)
            switches = [
                i
                for i in model.requests[-1].items
                if isinstance(i, ContextItem) and i.content_kind == "model_switch.instructions"
            ]
            assert len(switches) == int(next_model != "small")
            if switches:
                assert switches[0].content.startswith("<model_switch>")
                assert "INITIAL RULES" in switches[0].content
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("cold", [False, True])
@pytest.mark.parametrize("next_model", ["small", "large"])
def test_compaction_model_diff_uses_previous_model_not_initial_base(tmp_path, cold, next_model):
    async def scenario():
        configured = replace(
            settings(tmp_path),
            model_contexts=(
                ModelContextInfo("large", 200_000, base_instructions="INITIAL RULES"),
                ModelContextInfo("small", 200_000, base_instructions="SMALL RULES"),
            ),
        )
        model = Model()
        runtime = make_runtime(tmp_path, model, configured=configured)
        try:
            assert isinstance([e async for e in runtime.stream("initial")][-1], TurnCompleted)
            await runtime.update_thread_settings(model="small")
            assert isinstance([e async for e in runtime.stream("changed")][-1], TurnCompleted)
            for _ in range(2):
                assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
                assert model.requests[-1].instructions == "INITIAL RULES"
            prefix = await runtime._repository.load_items(runtime.thread_id)
            if cold:
                thread = runtime.thread_id
                await runtime.aclose()
                runtime = make_runtime(
                    tmp_path, model, configured=replace(configured, model=next_model), thread=thread
                )
            else:
                await runtime.update_thread_settings(model=next_model)
            assert isinstance([e async for e in runtime.stream("after compact")][-1], TurnCompleted)
            switches = [
                item
                for item in model.requests[-1].items
                if isinstance(item, ContextItem)
                and item.content_kind == "model_switch.instructions"
            ]
            assert len(switches) == int(next_model != "small")
            if switches:
                assert "INITIAL RULES" in switches[0].content
            assert model.requests[-1].instructions == "INITIAL RULES"
            stored = await runtime._repository.load_items(runtime.thread_id)
            assert stored[: len(prefix)] == prefix
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("pause_after_commit", [False, True])
def test_external_cancellation_joins_base_write_before_releasing_writer(
    tmp_path, monkeypatch, pause_after_commit
):
    async def scenario():
        configured = replace(
            settings(tmp_path),
            model_contexts=(ModelContextInfo("large", 200_000, base_instructions="OWNED BASE"),),
        )
        model = Model()
        runtime = make_runtime(tmp_path, model, configured=configured)
        original = runtime._repository._ensure_base_instructions
        loop = asyncio.get_running_loop()
        entered = asyncio.Event()
        release = threading.Event()
        finished = threading.Event()

        def gated_write(*args):
            try:
                saved = original(*args) if pause_after_commit else None
                loop.call_soon_threadsafe(entered.set)
                if not release.wait(10):
                    raise TimeoutError("test did not release base write")
                return saved if pause_after_commit else original(*args)
            finally:
                finished.set()

        monkeypatch.setattr(runtime._repository, "_ensure_base_instructions", gated_write)
        pending = asyncio.create_task(runtime._ensure_ready())
        try:
            await asyncio.wait_for(entered.wait(), 5)
            for _ in range(2):
                pending.cancel()
                # Yield for cancellation delivery, not a wall-clock race timeout.
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                assert not pending.done()
                assert runtime._writer.held
                assert not finished.is_set()
                assert model.requests == []
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(pending, 5)
            assert finished.is_set()
            assert not runtime._writer.held
            assert runtime._compiled is None
            assert model.requests == []
            cold = make_runtime(
                tmp_path,
                model,
                configured=replace(
                    configured,
                    model_contexts=(
                        ModelContextInfo("large", 200_000, base_instructions="LATER BASE"),
                    ),
                ),
                thread=runtime.thread_id,
            )
            try:
                assert isinstance([e async for e in cold.stream("cold")][-1], TurnCompleted)
                assert len(model.requests) == 1
                assert model.requests[0].instructions == "OWNED BASE"
            finally:
                await cold.aclose()
        finally:
            release.set()
            await asyncio.gather(pending, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("after_commit", [False, True])
@pytest.mark.parametrize("failure_type", [OSError, asyncio.CancelledError])
def test_base_initialization_failure_recovers_actual_commit(
    tmp_path, monkeypatch, after_commit, failure_type
):
    async def scenario():
        configured = replace(
            settings(tmp_path),
            model_contexts=(ModelContextInfo("large", 200_000, base_instructions="FIRST BASE"),),
        )
        model = Model()
        runtime = make_runtime(tmp_path, model, configured=configured)
        repository = runtime._repository
        original = repository._ensure_base_instructions
        failure = failure_type("base persistence interrupted")

        def fail_write(*args):
            if after_commit:
                original(*args)
            raise failure

        monkeypatch.setattr(repository, "_ensure_base_instructions", fail_write)
        try:
            with pytest.raises(failure_type):
                await runtime._ensure_ready()
            assert model.requests == []
            thread = runtime.thread_id
            with repository._connect() as connection:
                rows = connection.execute(
                    "SELECT model, instructions FROM thread_base_instructions WHERE thread_id = ?",
                    (str(thread),),
                ).fetchall()
            assert [tuple(row) for row in rows] == (
                [("large", "FIRST BASE")] if after_commit else []
            )
            # Reopen before closing the failed Runtime: rollback must already
            # have released its writer, not rely on a later explicit close.
            cold = make_runtime(
                tmp_path,
                model,
                configured=replace(
                    configured,
                    model_contexts=(
                        ModelContextInfo("large", 200_000, base_instructions="NEW CATALOG BASE"),
                    ),
                ),
                thread=thread,
            )
            try:
                assert isinstance([e async for e in cold.stream("recovered")][-1], TurnCompleted)
                assert len(model.requests) == 1
                expected = "FIRST BASE" if after_commit else "NEW CATALOG BASE"
                assert model.requests[0].instructions == expected
                with cold._repository._connect() as connection:
                    rows = connection.execute(
                        "SELECT model, instructions FROM thread_base_instructions "
                        "WHERE thread_id = ?",
                        (str(thread),),
                    ).fetchall()
                assert [tuple(row) for row in rows] == [("large", expected)]
            finally:
                await cold.aclose()
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("target_instructions", ["TARGET MODEL RULES", ""])
def test_local_model_instructions_keep_base_and_append_switch_once(tmp_path, target_instructions):
    async def scenario():
        catalog = parse_model_contexts(
            {
                "large": {
                    "context_window": 200_000,
                    "base_instructions": "INITIAL MODEL RULES",
                },
                "small": {
                    "context_window": 200_000,
                    "base_instructions": target_instructions,
                },
            }
        )
        configured = replace(settings(tmp_path), model_contexts=catalog)
        model = Model()
        runtime = make_runtime(tmp_path, model, configured=configured)
        try:
            assert isinstance([e async for e in runtime.stream("first")][-1], TurnCompleted)
            assert model.requests[0].instructions == "INITIAL MODEL RULES"
            assert not any(
                isinstance(item, ContextItem) and item.content_kind == "model_switch.instructions"
                for item in model.requests[0].items
            )
            await runtime.update_thread_settings(model="small")
            for _ in range(2):
                assert isinstance([e async for e in runtime.stream("next")][-1], TurnCompleted)
            thread = runtime.thread_id
            prefix = await runtime._repository.load_items(thread)
            await runtime.aclose()
            runtime = make_runtime(
                tmp_path, model, configured=replace(configured, model="small"), thread=thread
            )
            assert isinstance([e async for e in runtime.stream("cold")][-1], TurnCompleted)
            stored = await runtime._repository.load_items(thread)
            assert stored[: len(prefix)] == prefix
            for request in model.requests[1:]:
                assert request.model == "small"
                assert request.instructions == "INITIAL MODEL RULES"
                switches = [
                    item
                    for item in request.items
                    if isinstance(item, ContextItem)
                    and item.content_kind == "model_switch.instructions"
                ]
                assert len(switches) == int(bool(target_instructions))
                if target_instructions:
                    assert switches[0].role.value == "developer"
                    assert switches[0].separate_message
                    assert switches[0].content.startswith("<model_switch>")
                    assert target_instructions in switches[0].content
                    assert switches[0].content.endswith("</model_switch>")
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("storage", ["same", "external", "ephemeral"])
@pytest.mark.parametrize("before", [None, 0])
def test_fork_inherits_base_instructions_even_when_target_model_differs(tmp_path, storage, before):
    async def scenario():
        configured = replace(
            settings(tmp_path),
            model_contexts=(
                ModelContextInfo("large", 200_000, base_instructions="SOURCE BASE"),
                ModelContextInfo("small", 200_000, base_instructions="TARGET BASE"),
            ),
        )
        source = make_runtime(tmp_path, Model(), configured=configured)
        try:
            assert isinstance([e async for e in source.stream("original")][-1], TurnCompleted)
            original = await source._repository.load_items(source.thread_id)
            model = Model()
            target = await LangGraphRuntime.acreate(
                settings=replace(configured, model="small"),
                model=model,
                registry=ToolRegistry(),
                database_path=tmp_path / ("sessions.db" if storage == "same" else "target.db"),
                home_path=tmp_path / "home",
                ephemeral=storage == "ephemeral",
                fork_from_thread_id=source.thread_id,
                fork_before_user_message=before,
                fork_source_repository=None if storage == "same" else source._repository,
            )
            try:
                for _ in range(2):
                    assert isinstance([e async for e in target.stream("fork")][-1], TurnCompleted)
                    assert model.requests[-1].instructions == "SOURCE BASE"
                assert await source._repository.load_items(source.thread_id) == original
            finally:
                await target.aclose()
        finally:
            await source.aclose()

    asyncio.run(scenario())
