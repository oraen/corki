import asyncio
import sqlite3
from dataclasses import replace

import pytest

from corki.context import ContextSnapshot, ContextWindowManager, active_history
from corki.protocol.ids import new_item_id, new_thread_id, new_turn_id
from corki.protocol.items import (
    CompactionItem,
    ContextItem,
    ContextRole,
    UserMessageItem,
    item_from_payload,
    item_to_payload,
)
from corki.storage import SQLiteSessionRepository


class NoSampling:
    async def stream(self, request):
        raise AssertionError("this small context must not compact")
        yield


def test_legacy_context_updates_preserve_prefix_and_render_removal():
    turn = new_turn_id()
    old = ContextItem("project.agents", ContextRole.USER, "old rule", turn)
    new = replace(old, id=new_item_id(), content="new rule")
    removed = replace(old, id=new_item_id(), content="")
    first = active_history((old,))
    second = active_history((old, new))
    third = active_history((old, new, removed))
    assert second[: len(first)] == first
    assert third[: len(second)] == second
    assert "replace all previously provided AGENTS.md instructions" in second[-1].content
    assert "previously provided AGENTS.md instructions no longer apply" in third[-1].content
    assert active_history(third) == third
    # Projection must not rewrite legacy append-only payloads.
    assert old.content == "old rule" and new.content == "new rule" and removed.content == ""
    assert "snapshot_content" not in item_to_payload(old)
    assert item_from_payload("context", item_to_payload(old)) == old


@pytest.mark.parametrize("old_role", [ContextRole.DEVELOPER, ContextRole.USER])
def test_same_text_role_change_clears_old_role_and_survives_reopen(tmp_path, old_role):
    async def scenario():
        thread, turn = new_thread_id(), new_turn_id()
        repository = SQLiteSessionRepository(tmp_path / "context.db")
        await repository.create_thread(thread, tmp_path)
        old = ContextItem("extension.rules", old_role, "same instruction", turn)
        await repository.append_items(thread, (old,))
        new_role = ContextRole.USER if old_role is ContextRole.DEVELOPER else ContextRole.DEVELOPER
        current = replace(old, id=new_item_id(), role=new_role)

        async def prepare():
            manager = ContextWindowManager(
                repository=repository,
                model=NoSampling(),
                model_name="fixture",
                context_window_tokens=100_000,
            )
            return await manager.prepare(
                thread_id=thread,
                turn_id=turn,
                snapshot=ContextSnapshot("base", (current,), tmp_path),
                tools=(),
            )

        try:
            result = await prepare()
            assert len(result.items) == 3
            assert result.items[0] == old
            assert result.items[1].role == old_role
            assert "no longer apply" in result.items[1].content
            assert result.items[2].role == new_role
            assert result.items[2].content == current.content
            assert len({i.id for i in result.items}) == 3
            stored = await repository.load_items(thread)
            await repository.close()
            repository = SQLiteSessionRepository(tmp_path / "context.db")
            repeated = await prepare()
            assert repeated.items == result.items
            assert await repository.load_items(thread) == stored
        finally:
            await repository.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("cancelled", [False, True])
def test_update_baseline_and_pending_input_share_commit_boundary(tmp_path, committed, cancelled):
    async def scenario():
        thread, turn = new_thread_id(), new_turn_id()
        repository = SQLiteSessionRepository(tmp_path / "context.db")
        await repository.create_thread(thread, tmp_path)
        old = ContextItem("project.agents", ContextRole.USER, "old rule", turn)
        await repository.append_items(thread, (old,))
        current = replace(old, id=new_item_id(), content="new rule")
        user = UserMessageItem("pending user", turn)
        manager = ContextWindowManager(
            repository=repository,
            model=NoSampling(),
            model_name="fixture",
            context_window_tokens=100_000,
        )
        append = repository.append_items
        armed = True

        async def fail_once(thread_id, items):
            nonlocal armed
            if armed:
                armed = False
                if committed:
                    await append(thread_id, items)
                if cancelled:
                    raise asyncio.CancelledError()
                raise OSError("append boundary fixture")
            await append(thread_id, items)

        repository.append_items = fail_once

        async def prepare():
            return await manager.prepare(
                thread_id=thread,
                turn_id=turn,
                snapshot=ContextSnapshot("base", (current,), tmp_path),
                tools=(),
                pending_items=(user,),
            )

        try:
            with pytest.raises(asyncio.CancelledError if cancelled else OSError):
                await prepare()
            interrupted = await repository.load_items(thread)
            assert len(interrupted) == (3 if committed else 1)
            result = await prepare()
            stored = await repository.load_items(thread)
            assert result.items == stored
            assert len(stored) == 3 and stored[0] == old and stored[-1] == user
            assert stored[1].snapshot_content == "new rule"
            assert "replace all previously provided" in stored[1].content
            assert (await prepare()).items == stored
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_old_database_projection_does_not_rewrite_legacy_payloads(tmp_path):
    async def scenario():
        thread, turn = new_thread_id(), new_turn_id()
        path = tmp_path / "context.db"
        repository = SQLiteSessionRepository(path)
        await repository.create_thread(thread, tmp_path)
        old = ContextItem("project.agents", ContextRole.USER, "old rule", turn)
        legacy = (
            old,
            replace(old, id=new_item_id(), content="new rule"),
            replace(old, id=new_item_id(), content=""),
        )
        await repository.append_items(thread, legacy)
        await repository.close()

        def payloads():
            with sqlite3.connect(path) as connection:
                return connection.execute(
                    "SELECT payload_json FROM conversation_items ORDER BY sequence"
                ).fetchall()

        original = payloads()
        assert all("snapshot_content" not in row[0] for row in original)
        repository = SQLiteSessionRepository(path)
        try:
            manager = ContextWindowManager(
                repository=repository,
                model=NoSampling(),
                model_name="fixture",
                context_window_tokens=100_000,
            )
            prepared = await manager.prepare(
                thread_id=thread,
                turn_id=turn,
                snapshot=ContextSnapshot("base", (), tmp_path),
                tools=(),
            )
            assert len(prepared.items) == 3
            assert "no longer apply" in prepared.items[-1].content
            await repository.append_items(thread, legacy)
            assert await repository.load_items(thread) == legacy
            assert payloads() == original
        finally:
            await repository.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("retained", [False, True])
def test_compaction_baseline_uses_only_replacement_window(tmp_path, retained):
    async def scenario():
        thread, turn = new_thread_id(), new_turn_id()
        repository = SQLiteSessionRepository(tmp_path / "context.db")
        await repository.create_thread(thread, tmp_path)
        old = ContextItem("project.agents", ContextRole.USER, "same rule", turn)
        marker = CompactionItem("summary", old.id, turn)
        replacement = (replace(old, id=new_item_id()),) if retained else ()
        await repository.append_items(thread, (old, marker, *replacement))
        try:
            manager = ContextWindowManager(
                repository=repository,
                model=NoSampling(),
                model_name="fixture",
                context_window_tokens=100_000,
            )
            snapshot = ContextSnapshot("base", (replace(old, id=new_item_id()),), tmp_path)
            prepared = await manager.prepare(
                thread_id=thread,
                turn_id=turn,
                snapshot=snapshot,
                tools=(),
            )
            contexts = [i for i in prepared.items if isinstance(i, ContextItem)]
            assert len(contexts) == 1
            assert contexts[0].content == "same rule"
            assert contexts[0].id != old.id
            stored = await repository.load_items(thread)
            assert stored[:2] == (old, marker)
            again = await manager.prepare(
                thread_id=thread,
                turn_id=turn,
                snapshot=snapshot,
                tools=(),
            )
            assert again.items == prepared.items
            assert await repository.load_items(thread) == stored
        finally:
            await repository.close()

    asyncio.run(scenario())
