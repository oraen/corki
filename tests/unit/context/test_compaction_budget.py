"""ModelInfo headroom and compact.rs oldest-item fallback behavior."""

import asyncio
from pathlib import Path

import pytest

from corki.context import ContextSnapshot, ContextWindowManager, estimate_request_tokens
from corki.models import ModelCompleted, ModelError, ModelErrorKind
from corki.protocol.ids import ToolCallId, new_thread_id, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    ContextRole,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall
from corki.storage import SQLiteSessionRepository


class SummaryModel:
    def __init__(self, *, reject_once=False, hang_after_completion=False):
        self.requests = []
        self.reject_once = reject_once
        self.hang_after_completion = hang_after_completion
        self.closed = 0

    async def stream(self, request):
        self.requests.append(request)
        try:
            if self.reject_once and len(self.requests) == 1:
                raise ModelError("provider context exceeded", kind=ModelErrorKind.CONTEXT_WINDOW)
            yield ModelCompleted(
                (
                    AssistantMessageItem(
                        "summary of recent facts", request.items[-1].turn_id, new_step_id()
                    ),
                )
            )
            if self.hang_after_completion:
                await asyncio.Event().wait()
        finally:
            self.closed += 1


def test_usable_window_headroom_rejects_request_that_fits_raw_window(tmp_path: Path):
    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "headroom.db")
        thread, turn = new_thread_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        model = SummaryModel()
        manager = ContextWindowManager(
            repository=repository,
            model=model,
            model_name="fixture",
            context_window_tokens=1000,
            auto_compact_tokens=980,
        )
        with pytest.raises(ModelError, match="950") as raised:
            await manager.prepare(
                thread_id=thread,
                turn_id=turn,
                snapshot=ContextSnapshot("i" * 3800, (), tmp_path),
                tools=(),
                pending_items=(UserMessageItem("hello", turn),),
            )
        assert raised.value.kind is ModelErrorKind.CONTEXT_WINDOW
        assert model.requests == []

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["cancel", "output_limit", "protocol"])
def test_compaction_failure_closes_stream_without_committing_replacement(tmp_path, failure):
    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "atomic.db")
        thread, old, turn = new_thread_id(), new_turn_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        original = (
            ContextItem("project.rules", ContextRole.DEVELOPER, "PRESERVE RULE", old),
            AssistantMessageItem("old answer " * 200, old, new_step_id()),
        )
        await repository.append_items(thread, original)
        started = asyncio.Event()

        class Iterator:
            closed = False

            def __aiter__(self):
                return self

            async def __anext__(self):
                started.set()
                if failure == "cancel":
                    await asyncio.Event().wait()
                raise ModelError(
                    "fixture failure",
                    kind=ModelErrorKind.OUTPUT_LIMIT
                    if failure == "output_limit"
                    else ModelErrorKind.PROTOCOL,
                )

            async def aclose(self):
                self.closed = True

        class Model:
            requests = []
            iterator = Iterator()

            def stream(self, request):
                self.requests.append(request)
                assert any(
                    isinstance(item, ContextItem) and item.content == "PRESERVE RULE"
                    for item in request.items
                )
                return self.iterator

        model = Model()
        manager = ContextWindowManager(
            repository=repository,
            model=model,
            model_name="fixture",
            context_window_tokens=3000,
            auto_compact_tokens=500,
            max_retries=0,  # This test isolates single-attempt failure atomicity.
        )
        invocation = asyncio.create_task(
            manager.prepare(
                thread_id=thread,
                turn_id=turn,
                snapshot=ContextSnapshot("base", (), tmp_path),
                tools=(),
                pending_items=(UserMessageItem("new request", turn),),
            )
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        if failure == "cancel":
            invocation.cancel()
        with pytest.raises(asyncio.CancelledError if failure == "cancel" else ModelError):
            await invocation
        assert model.iterator.closed
        assert len(model.requests) == 1
        assert await repository.load_items(thread) == original

    asyncio.run(scenario())


def test_mid_turn_current_input_is_not_truncated_to_make_summary_fit(tmp_path: Path):
    class LongSummaryModel(SummaryModel):
        async def stream(self, request):
            self.requests.append(request)
            yield ModelCompleted(
                (AssistantMessageItem("summary " * 100, request.items[-1].turn_id, new_step_id()),)
            )

    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "protected.db")
        thread, turn = new_thread_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        current = UserMessageItem("constraint" * 320, turn)
        original = (current, AssistantMessageItem("old output " * 1000, turn, new_step_id()))
        await repository.append_items(thread, original)
        model = LongSummaryModel()
        manager = ContextWindowManager(
            repository=repository,
            model=model,
            model_name="fixture",
            context_window_tokens=1000,
            auto_compact_tokens=700,
        )
        with pytest.raises(ModelError) as raised:
            await manager.prepare(
                thread_id=thread,
                turn_id=turn,
                snapshot=ContextSnapshot("b" * 400, (), tmp_path),
                tools=(),
            )
        assert raised.value.kind is ModelErrorKind.CONTEXT_WINDOW
        assert await repository.load_items(thread) == original
        assert all(current in request.items for request in model.requests)

    asyncio.run(scenario())


def test_auto_compact_limit_is_capped_at_ninety_percent(tmp_path: Path):
    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "threshold.db")
        thread, old, turn = new_thread_id(), new_turn_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        await repository.append_items(
            thread,
            (
                AssistantMessageItem("x" * 1800, old, new_step_id()),
                AssistantMessageItem("y" * 1800, old, new_step_id()),
            ),
        )
        model = SummaryModel()
        manager = ContextWindowManager(
            repository=repository,
            model=model,
            model_name="fixture",
            context_window_tokens=1000,
            auto_compact_tokens=980,
        )
        prepared = await manager.prepare(
            thread_id=thread,
            turn_id=turn,
            snapshot=ContextSnapshot("base", (), tmp_path),
            tools=(),
            pending_items=(UserMessageItem("hello", turn),),
        )
        assert prepared.compacted
        assert prepared.estimated_tokens < 950

    asyncio.run(scenario())


@pytest.mark.parametrize("provider_overflow", [False, True])
def test_compaction_only_reduces_oldest_pair_after_provider_overflow(
    tmp_path: Path, provider_overflow
):
    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "overflow.db")
        thread, old, turn = new_thread_id(), new_turn_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        call = ToolCall(ToolCallId("old-tool"), "read", {})
        original = (
            ToolCallItem(call, old, new_step_id()),
            ToolResultItem(call.id, call.name, "x" * (900 if provider_overflow else 10000), old),
            AssistantMessageItem("RECENT FACT", old, new_step_id()),
        )
        await repository.append_items(thread, original)
        current = UserMessageItem("CURRENT INPUT MUST REMAIN VERBATIM", turn)
        model = SummaryModel(reject_once=provider_overflow)
        manager = ContextWindowManager(
            repository=repository,
            model=model,
            model_name="fixture",
            context_window_tokens=800,
            auto_compact_tokens=200,
        )
        prepared = await manager.prepare(
            thread_id=thread,
            turn_id=turn,
            snapshot=ContextSnapshot("BASE INSTRUCTIONS", (), tmp_path),
            tools=(),
            pending_items=(current,),
        )
        assert prepared.compacted
        assert current in prepared.items
        assert (await repository.load_items(thread))[:3] == original
        assert len(model.requests) == (2 if provider_overflow else 1)
        for request in model.requests:
            assert request.instructions == "BASE INSTRUCTIONS"
            assert request.tools == ()
            assert isinstance(request.items[-1], UserMessageItem)
            assert "checkpoint compaction" in request.items[-1].content
            assert current.id not in {item.id for item in request.items}
            if provider_overflow:
                assert (
                    estimate_request_tokens(request.instructions, request.items, request.tools)
                    < 760
                )
            calls = {item.call.id for item in request.items if isinstance(item, ToolCallItem)}
            results = {item.call_id for item in request.items if isinstance(item, ToolResultItem)}
            assert calls == results
        assert any(
            isinstance(item, (ToolCallItem, ToolResultItem)) for item in model.requests[-1].items
        ) == (not provider_overflow)
        if not provider_overflow:
            request = model.requests[0]
            assert estimate_request_tokens(request.instructions, request.items, ()) > 760
        assert any(
            isinstance(item, AssistantMessageItem) and item.content == "RECENT FACT"
            for item in model.requests[-1].items
        )
        assert model.closed == len(model.requests)

    asyncio.run(scenario())


def test_compaction_stops_and_closes_at_authoritative_completion(tmp_path: Path):
    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "stream.db")
        thread, old, turn = new_thread_id(), new_turn_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        await repository.append_items(
            thread,
            (
                AssistantMessageItem("x" * 900, old, new_step_id()),
                AssistantMessageItem("recent", old, new_step_id()),
            ),
        )
        model = SummaryModel(hang_after_completion=True)
        manager = ContextWindowManager(
            repository=repository,
            model=model,
            model_name="fixture",
            context_window_tokens=2000,
            auto_compact_tokens=200,
        )
        prepared = await asyncio.wait_for(
            manager.prepare(
                thread_id=thread,
                turn_id=turn,
                snapshot=ContextSnapshot("base", (), tmp_path),
                tools=(),
                pending_items=(UserMessageItem("continue", turn),),
            ),
            timeout=0.2,
        )
        assert prepared.compacted
        assert model.closed == 1

    asyncio.run(scenario())
