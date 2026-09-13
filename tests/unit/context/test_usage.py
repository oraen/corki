import asyncio
import sqlite3

import pytest

from corki.context.tokens import estimate_item_tokens
from corki.context.usage import context_tokens_from_usage
from corki.models import ModelCompleted
from corki.models.types import ModelUsage
from corki.protocol.ids import ItemId, ThreadId, TurnId, new_tool_call_id
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    ReasoningItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.sessions.models import ContextUsage
from corki.storage import SQLiteSessionRepository


@pytest.mark.parametrize("total", [None, 0, 789])
def test_usage_total_is_latest_context_not_billing_subsets(total):
    usage = ModelUsage(100, 20, 90, 15, total_tokens=total)
    assert usage.context_tokens == (120 if total is None else total)
    assert ModelUsage().context_tokens is None


@pytest.mark.parametrize("bad", [-(2**63) - 1, True, 1.5, "100"])
def test_usage_rejects_invalid_total(bad):
    with pytest.raises(ValueError):
        ModelUsage(total_tokens=bad)


def test_local_tail_counted_once_and_compaction_invalidates_anchor():
    turn = TurnId("turn")
    answer = AssistantMessageItem("already counted", turn, new_step_id())
    user = UserMessageItem("local user", turn)
    output = ToolResultItem(new_tool_call_id(), "echo", "local observation", False, turn)
    usage = ContextUsage(100, answer.id, True)
    assert context_tokens_from_usage(usage, (answer,), (answer, user, output)) == (
        100 + estimate_item_tokens(user) + estimate_item_tokens(output)
    )
    summary = CompactionItem("replacement", answer.id, turn)
    assert context_tokens_from_usage(usage, (answer, summary), (summary, user)) is None
    assert (
        context_tokens_from_usage(ContextUsage(100, ItemId("missing")), (answer,), (answer,))
        is None
    )


@pytest.mark.parametrize("included", [False, True])
def test_old_encrypted_reasoning_uses_ordinary_estimate_despite_legacy_flag(included):
    turn = TurnId("turn")
    old = ReasoningItem(
        "summary not a second cost", turn, new_step_id(), encrypted_content="x" * 4000
    )
    user = UserMessageItem("new instruction boundary", turn)
    current = ReasoningItem("current", turn, new_step_id(), encrypted_content="x" * 8000)
    answer = AssistantMessageItem("last", turn, new_step_id())
    items = (old, user, current, answer)
    assert estimate_item_tokens(old) == 588  # ceil((4000 * 3 / 4 - 650) / 4)
    assert context_tokens_from_usage(ContextUsage(100, answer.id, included), items, items) == (688)


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
def test_usage_roundtrip_latest_commit_and_legacy_migration(tmp_path, empty, legacy):
    async def scenario():
        path = tmp_path / "sessions.db"
        repository = SQLiteSessionRepository(path)
        thread, turn = ThreadId("thread"), TurnId("turn")
        await repository.create_thread(thread, tmp_path)
        user = UserMessageItem("input", turn)
        await repository.append_items(thread, (user,))
        first = AssistantMessageItem("first", turn, new_step_id())
        await repository.commit_model_step(
            thread, turn, 0, ModelCompleted((first,), ModelUsage(9000))
        )
        output = () if empty else (AssistantMessageItem("second", turn, new_step_id()),)
        completed = ModelCompleted(output, ModelUsage(10, 2, total_tokens=123))
        await repository.commit_model_step(thread, turn, 1, completed)
        await repository.commit_model_step(thread, turn, 1, completed)
        assert await repository.load_model_step(thread, turn, 1) == completed
        if legacy:
            with sqlite3.connect(path) as connection:
                connection.execute("ALTER TABLE model_steps DROP COLUMN total_tokens")
                connection.execute("ALTER TABLE model_steps DROP COLUMN usage_anchor_id")
        reopened = SQLiteSessionRepository(path)
        usage = await reopened.load_context_usage(thread)
        if legacy and empty:
            assert usage is None
        else:
            assert usage.total_tokens == (12 if legacy else 123)
            assert usage.anchor_id == (first.id if empty else output[-1].id)
        await reopened.commit_model_step(thread, turn, 2, ModelCompleted(()))
        assert await reopened.load_context_usage(thread) is None

    asyncio.run(scenario())


def test_usage_and_output_commit_roll_back_together(tmp_path, monkeypatch):
    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "sessions.db")
        thread, turn = ThreadId("thread"), TurnId("turn")
        await repository.create_thread(thread, tmp_path)
        user = UserMessageItem("input", turn)
        await repository.append_items(thread, (user,))
        append = repository._append_items_in_connection

        def fail_after_append(connection, thread_id, items):
            append(connection, thread_id, items)
            raise OSError("model transaction fault")

        monkeypatch.setattr(repository, "_append_items_in_connection", fail_after_append)
        with pytest.raises(OSError):
            await repository.commit_model_step(
                thread,
                turn,
                0,
                ModelCompleted(
                    (AssistantMessageItem("answer", turn, new_step_id()),), ModelUsage(9999)
                ),
            )
        assert await repository.load_context_usage(thread) is None
        assert await repository.load_model_step(thread, turn, 0) is None
        assert await repository.load_items(thread) == (user,)

    asyncio.run(scenario())
