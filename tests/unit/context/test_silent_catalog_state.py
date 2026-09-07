import asyncio
from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.context import ContextSnapshot, ContextWindowManager, active_history
from corki.context.tokens import estimate_item_tokens
from corki.context.world_state import changed_context_items
from corki.core.checkpoint import checkpoint_serializer
from corki.models import ModelCompleted
from corki.protocol.ids import new_thread_id, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    ContextItem,
    ContextRole,
    UserMessageItem,
    item_from_payload,
    item_to_payload,
    new_step_id,
)
from corki.storage import SQLiteSessionRepository


def catalog(turn, *, body="", listed=True):
    return ContextItem(
        "extensions.skills.catalog",
        ContextRole.DEVELOPER,
        body,
        turn,
        snapshot_state="skills.listed" if listed else "skills.hidden",
    )


@pytest.mark.parametrize("listed", [False, True])
def test_initial_empty_state_is_silent_but_later_mode_change_has_a_notice(listed):
    turn = new_turn_id()
    first = catalog(turn, listed=listed)
    stored = changed_context_items((), (first,), turn)
    assert len(stored) == 1 and stored[0].content == ""
    assert active_history(stored) == ()
    assert estimate_item_tokens(stored[0]) == 0
    assert changed_context_items(stored, (first,), turn) == ()
    following = catalog(turn, listed=not listed)
    updates = changed_context_items(stored, (following,), turn)
    assert len(updates) == 1 and updates[0].snapshot_content == ""
    assert ("No host skills" if not listed else "not listed automatically") in updates[0].content
    assert active_history((*stored, *updates)) == updates
    assert changed_context_items((*stored, *updates), (following,), turn) == ()


def test_comparison_state_round_trips_without_changing_legacy_payloads():
    legacy = ContextItem("legacy", ContextRole.USER, "body", new_turn_id())
    assert "snapshot_state" not in item_to_payload(legacy)
    assert item_to_payload(
        item_from_payload("context", item_to_payload(legacy))
    ) == item_to_payload(legacy)
    current = catalog(legacy.turn_id, listed=False)
    assert item_from_payload("context", item_to_payload(current)) == current
    serializer = checkpoint_serializer()
    assert serializer.loads_typed(serializer.dumps_typed(current)) == current


def test_silent_state_does_not_split_legacy_assistant_groups(tmp_path):
    async def scenario():
        thread, turn, step = new_thread_id(), new_turn_id(), new_step_id()
        repo = SQLiteSessionRepository(tmp_path / "sessions.db")
        try:
            await repo.create_thread(thread, tmp_path)
            await repo.append_items(
                thread,
                (
                    AssistantMessageItem("first", turn, step),
                    catalog(turn, listed=False),
                    AssistantMessageItem("second", turn, step),
                ),
            )
            messages = await repo.load_messages(thread)
            assert len(messages) == 1 and messages[0].content == "firstsecond"
        finally:
            await repo.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("value", ["false", 0, None])
def test_catalog_visibility_setting_requires_a_boolean(tmp_path, value):
    with pytest.raises(ValueError, match="skills.include_instructions"):
        CorkiSettings(working_directory=tmp_path, skills_include_instructions=value)


def test_compaction_rebuilds_silent_state_without_old_catalog_or_hidden_notice(tmp_path):
    async def scenario():
        thread, turn = new_thread_id(), new_turn_id()
        repo = SQLiteSessionRepository(tmp_path / "sessions.db")
        await repo.create_thread(thread, tmp_path)
        old = catalog(turn, body="OLD LISTED CATALOG")
        await repo.append_items(
            thread,
            (
                old,
                UserMessageItem("old " * 4000, turn),
                AssistantMessageItem("finished", turn, new_step_id()),
            ),
        )
        calls = []

        class Summary:
            async def stream(self, request):
                calls.append(request)
                yield ModelCompleted((AssistantMessageItem("summary", turn, new_step_id()),))

        manager = ContextWindowManager(
            repository=repo,
            model=Summary(),
            model_name="fixture",
            context_window_tokens=10_000,
            auto_compact_tokens=1000,
        )
        next_turn = new_turn_id()
        snapshot = ContextSnapshot("base", (catalog(next_turn, listed=False),), tmp_path)
        pending = UserMessageItem("current request", next_turn)
        try:
            prepared = await manager.prepare(
                thread_id=thread,
                turn_id=next_turn,
                snapshot=snapshot,
                tools=(),
                pending_items=(pending,),
            )
            assert prepared.compacted and len(calls) == 1
            assert not any(isinstance(i, ContextItem) for i in prepared.items)
            assert pending in prepared.items
            stored = await repo.load_items(thread)
            marker = max(i for i, item in enumerate(stored) if isinstance(item, CompactionItem))
            retained = [i for i in stored[marker + 1 :] if isinstance(i, ContextItem)]
            assert len(retained) == 1 and retained[0].snapshot_state == "skills.hidden"
            assert retained[0].content == ""
            messages = await repo.load_messages(thread)
            assert not any(str(m.id) == str(retained[0].id) for m in messages)
            assert changed_context_items(stored, snapshot.items, next_turn) == ()
            await repo.close()
            repo = SQLiteSessionRepository(tmp_path / "sessions.db")
            restored = await repo.load_items(thread)
            assert active_history(restored) == prepared.items
            visible = replace(snapshot.items[0], snapshot_state="skills.listed")
            updates = changed_context_items(restored, (visible,), next_turn)
            assert len(updates) == 1 and "No host skills" in updates[0].content
        finally:
            await repo.close()

    asyncio.run(scenario())
