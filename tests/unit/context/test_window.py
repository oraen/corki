import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from corki.context import ContextSnapshot, ContextWindowManager, active_history
from corki.models import ModelCompleted, ModelRequest
from corki.models.types import ModelEvent
from corki.protocol.ids import new_thread_id, new_tool_call_id, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
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
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        yield ModelCompleted(
            (
                AssistantMessageItem(
                    "durable compact summary", request.items[-1].turn_id, new_step_id()
                ),
            )
        )

    async def aclose(self) -> None:
        return None


def test_window_compacts_without_history_gaps_and_persists_checkpoint(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        repository = SQLiteSessionRepository(tmp_path / "context.db")
        thread_id = new_thread_id()
        turn_id = new_turn_id()
        await repository.create_thread(thread_id, tmp_path)
        original = (
            UserMessageItem("old request " + "x" * 1_200, turn_id),
            AssistantMessageItem("old response " + "y" * 1_200, turn_id, new_step_id()),
        )
        await repository.append_items(thread_id, original)
        snapshot = ContextSnapshot(
            "base",
            (ContextItem("environment", ContextRole.USER, "cwd facts", turn_id),),
            tmp_path,
        )
        model = SummaryModel()
        manager = ContextWindowManager(
            repository=repository,
            model=model,
            model_name="test",
            context_window_tokens=3_000,
            auto_compact_tokens=500,
        )

        prepared = await manager.prepare(
            thread_id=thread_id, turn_id=turn_id, snapshot=snapshot, tools=()
        )
        stored = await repository.load_items(thread_id)

        assert prepared.compacted
        assert isinstance(prepared.items[0], ContextItem)
        assert isinstance(prepared.items[1], UserMessageItem)
        assert prepared.items[1].content == original[0].content
        assert isinstance(prepared.items[2], CompactionItem)
        assert prepared.items[2].summary == "durable compact summary"
        assert original == stored[:2]
        assert active_history(stored) == prepared.items
        assert len(model.requests) == 1
        await repository.close()

    asyncio.run(scenario())


def test_removed_context_source_appends_notice_without_rewriting_history(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = SQLiteSessionRepository(tmp_path / "context.db")
        thread_id = new_thread_id()
        turn_id = new_turn_id()
        await repository.create_thread(thread_id, tmp_path)
        model = SummaryModel()
        manager = ContextWindowManager(
            repository=repository,
            model=model,
            model_name="test",
            context_window_tokens=100_000,
            auto_compact_tokens=90_000,
        )
        first = ContextSnapshot(
            "base",
            (ContextItem("project.agents", ContextRole.USER, "old rule", turn_id),),
            tmp_path,
        )
        await manager.prepare(thread_id=thread_id, turn_id=turn_id, snapshot=first, tools=())
        second_turn = new_turn_id()
        prepared = await manager.prepare(
            thread_id=thread_id,
            turn_id=second_turn,
            snapshot=ContextSnapshot("base", (), tmp_path),
            tools=(),
        )

        assert prepared.items[0] == first.items[0]
        assert (
            "previously provided AGENTS.md instructions no longer apply"
            in prepared.items[-1].content
        )
        stored = await repository.load_items(thread_id)
        assert any(
            isinstance(item, ContextItem)
            and item.key == "project.agents"
            and item.snapshot_content == ""
            for item in stored
        )
        await repository.close()

    asyncio.run(scenario())


def test_pending_user_input_is_atomically_appended_after_context_updates(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = SQLiteSessionRepository(tmp_path / "context.db")
        thread_id, turn_id = new_thread_id(), new_turn_id()
        await repository.create_thread(thread_id, tmp_path)
        manager = ContextWindowManager(
            repository=repository,
            model=SummaryModel(),
            model_name="test",
            context_window_tokens=100_000,
            auto_compact_tokens=90_000,
        )
        context = ContextItem("project.agents", ContextRole.USER, "rules", turn_id)
        user = UserMessageItem("implement the task", turn_id)

        prepared = await manager.prepare(
            thread_id=thread_id,
            turn_id=turn_id,
            snapshot=ContextSnapshot("base", (context,), tmp_path),
            tools=(),
            pending_items=(user,),
        )
        repeated = await manager.prepare(
            thread_id=thread_id,
            turn_id=turn_id,
            snapshot=ContextSnapshot("base", (context,), tmp_path),
            tools=(),
            pending_items=(user,),
        )
        stored = await repository.load_items(thread_id)

        assert prepared.items == (context, user)
        assert repeated.items == prepared.items
        assert stored == (context, user)
        await repository.close()

    asyncio.run(scenario())


def test_compaction_preserves_current_user_input_verbatim_after_checkpoint(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        repository = SQLiteSessionRepository(tmp_path / "context.db")
        thread_id, old_turn, current_turn = new_thread_id(), new_turn_id(), new_turn_id()
        await repository.create_thread(thread_id, tmp_path)
        old_items = (
            UserMessageItem("old request " + "x" * 1_200, old_turn),
            AssistantMessageItem("old response " + "y" * 1_200, old_turn, new_step_id()),
        )
        await repository.append_items(thread_id, old_items)
        model = SummaryModel()
        manager = ContextWindowManager(
            repository=repository,
            model=model,
            model_name="test",
            context_window_tokens=3_000,
            auto_compact_tokens=500,
        )
        context = ContextItem("project.agents", ContextRole.USER, "current rules", current_turn)
        current = UserMessageItem("CURRENT_UNCOMPACTED_REQUEST", current_turn)

        prepared = await manager.prepare(
            thread_id=thread_id,
            turn_id=current_turn,
            snapshot=ContextSnapshot("base", (context,), tmp_path),
            tools=(),
            pending_items=(current,),
        )
        stored = await repository.load_items(thread_id)

        assert prepared.compacted
        assert isinstance(prepared.items[0], UserMessageItem)
        assert prepared.items[0].content == old_items[0].content
        assert isinstance(prepared.items[1], CompactionItem)
        assert isinstance(prepared.items[2], ContextItem)
        assert prepared.items[2].content == context.content
        assert prepared.items[3] == current
        assert all(item.id != current.id for item in model.requests[0].items)
        assert active_history(stored) == prepared.items
        await repository.close()

    asyncio.run(scenario())


def test_compaction_recovery_does_not_resummarize_already_stored_pending_input(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        repository = SQLiteSessionRepository(tmp_path / "context.db")
        thread_id, old_turn, current_turn = new_thread_id(), new_turn_id(), new_turn_id()
        await repository.create_thread(thread_id, tmp_path)
        await repository.append_items(
            thread_id,
            (
                UserMessageItem("old " + "x" * 1_200, old_turn),
                AssistantMessageItem("reply " + "y" * 1_200, old_turn, new_step_id()),
            ),
        )
        context = ContextItem("environment", ContextRole.USER, "current", current_turn)
        current = UserMessageItem("DO_NOT_SUMMARIZE_ME", current_turn)
        high_threshold = ContextWindowManager(
            repository=repository,
            model=SummaryModel(),
            model_name="test",
            context_window_tokens=3_000,
            auto_compact_tokens=2_500,
        )
        # Mimic a legacy/pre-checkpoint state in which the pending input was
        # already persisted before a later prepare decides to compact.
        await high_threshold.prepare(
            thread_id=thread_id,
            turn_id=current_turn,
            snapshot=ContextSnapshot("base", (context,), tmp_path),
            tools=(),
            pending_items=(current,),
        )
        model = SummaryModel()
        manager = ContextWindowManager(
            repository=repository,
            model=model,
            model_name="test",
            context_window_tokens=3_000,
            auto_compact_tokens=500,
        )

        first = await manager.prepare(
            thread_id=thread_id,
            turn_id=current_turn,
            snapshot=ContextSnapshot("base", (context,), tmp_path),
            tools=(),
            pending_items=(current,),
        )
        repeated = await manager.prepare(
            thread_id=thread_id,
            turn_id=current_turn,
            snapshot=ContextSnapshot("base", (context,), tmp_path),
            tools=(),
            pending_items=(current,),
        )

        assert first.compacted
        assert not repeated.compacted
        assert len(model.requests) == 1
        copies = [item for item in first.items if isinstance(item, UserMessageItem)]
        assert (
            next(item for item in copies if item.content == current.content).retained_from_id
            == current.id
        )
        assert all(
            not isinstance(item, UserMessageItem) or item.content != current.content
            for item in model.requests[0].items
        )
        assert (
            sum(
                isinstance(item, UserMessageItem) and item.content == current.content
                for item in repeated.items
            )
            == 1
        )
        await repository.close()

    asyncio.run(scenario())


def test_history_normalizes_missing_and_orphan_tool_results() -> None:
    turn_id = new_turn_id()
    call = ToolCall(new_tool_call_id(), "tool", {})
    other = new_tool_call_id()
    items = (
        UserMessageItem("go", turn_id),
        ToolCallItem(call, turn_id, new_step_id()),
        ToolResultItem(other, "other", "orphan", turn_id),
    )

    normalized = active_history(items)

    assert len(normalized) == 3
    assert isinstance(normalized[-1], ToolResultItem)
    assert normalized[-1].call_id == call.id
    assert normalized[-1].is_error
