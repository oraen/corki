"""Display ownership is atomic, compactable only on the model side, and rebuildable."""

import asyncio
import json
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import replace

import pytest

from corki.protocol.ids import ThreadId
from corki.protocol.items import AssistantMessageItem, CompactionItem, UserMessageItem, new_step_id
from corki.storage.chat_history import VERSION, install_chat_history
from corki.storage.display_pages import read_display_items_page
from corki.storage.sqlite import SQLiteSessionRepository, StorageIntegrityError
from corki.storage.thread_delete import SQLiteThreadDeleteStore


def legacy(db):
    for trigger in (
        "chat_history_insert",
        "chat_turns_insert",
        "chat_turns_update",
        "chat_history_session_delete",
    ):
        db.execute("DROP TRIGGER " + trigger)
    for table in ("chat_history", "chat_history_ingest", "chat_turns"):
        db.execute("DROP TABLE " + table)
    db.execute("DELETE FROM schema_migrations WHERE name=?", (VERSION,))


def test_compaction_never_changes_display_pages_or_source_history(tmp_path):
    async def run():
        repo = SQLiteSessionRepository(tmp_path / "history.db")
        thread = ThreadId("fixture")
        await repo.create_thread(thread, tmp_path)
        originals = tuple(UserMessageItem(str(i), "turn") for i in range(83))
        await repo.append_items(thread, originals)
        before = await repo.load_display_items_page(thread, limit=20)
        copies = tuple(replace(i, id="copy-" + str(i.id), retained_from_id=i.id) for i in originals)
        await repo.append_items(
            thread,
            (CompactionItem("summary", None, "compact", replacement_item_count=len(copies)),),
        )
        # Incomplete replacement batches, reopen and retries must preserve both views.
        await repo.append_items(thread, copies[:40])
        assert await repo.load_display_items_page(thread, limit=20) == before
        repo = SQLiteSessionRepository(repo.path)
        await repo.append_items(thread, copies[40:])
        await repo.append_items(thread, copies[40:])
        assert await repo.load_display_items_page(thread, limit=20) == before
        all_visible = []
        cursor = None
        while True:
            page = await repo.load_display_items_page(thread, cursor=cursor, limit=13)
            all_visible = list(page.items) + all_visible
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        assert tuple(all_visible) == originals
        assert (await repo.load_items(thread))[:83] == originals
        assert len(await repo.load_items(thread)) == 167
        await repo.append_items(
            thread,
            (
                CompactionItem(
                    "second summary", None, "compact2", replacement_item_count=len(copies)
                ),
                *(replace(i, id="second-" + str(i.id)) for i in copies),
            ),
        )
        assert await repo.load_display_items_page(thread, limit=20) == before
        with repo._connect() as db:
            plan = " ".join(
                str(tuple(r))
                for r in db.execute(
                    "EXPLAIN QUERY PLAN SELECT * FROM chat_history "
                    "WHERE thread_id=? AND sequence<? ORDER BY sequence DESC LIMIT 20",
                    (thread, 80),
                )
            )
            assert "SEARCH chat_history USING PRIMARY KEY" in plan
        await SQLiteThreadDeleteStore(repo.path).delete(thread)
        with repo._connect() as db:
            assert db.execute("SELECT count(*) FROM chat_history").fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM chat_history_ingest").fetchone()[0] == 0

    asyncio.run(run())


def test_readonly_legacy_view_and_migration_are_lossless(tmp_path):
    async def run():
        repo = SQLiteSessionRepository(tmp_path / "legacy.db")
        thread = ThreadId("fixture")
        await repo.create_thread(thread, tmp_path)
        question = UserMessageItem("original", "turn")
        answer = AssistantMessageItem("original reply", "turn", new_step_id())
        items = (
            question,
            answer,
            CompactionItem("summary", None, "compact", replacement_item_count=3),
            replace(question, id="retained", retained_from_id=question.id),
            replace(answer, id="hidden-answer"),
            UserMessageItem("fresh inside replacement", "next"),
        )
        await repo.append_items(thread, items)
        with repo._connect() as db:
            original = [
                tuple(r) for r in db.execute("SELECT * FROM conversation_items ORDER BY sequence")
            ]
            legacy(db)

        @contextmanager
        def readonly():
            with closing(sqlite3.connect(repo.path.as_uri() + "?mode=ro", uri=True)) as db:
                db.row_factory = sqlite3.Row
                yield db

        with pytest.raises(ValueError, match="尚未迁移"):
            read_display_items_page(readonly, thread, None, 100)
        with repo._connect() as db:
            install_chat_history(db)
            install_chat_history(db)
            assert [
                tuple(r) for r in db.execute("SELECT * FROM conversation_items ORDER BY sequence")
            ] == original
        page = read_display_items_page(readonly, thread, None, 100)
        assert page.items == (question, answer, items[-1])
        assert await repo.load_display_items_page(thread, limit=100) == page
        with repo._connect() as db:
            db.execute("DELETE FROM conversation_items WHERE thread_id=?", (thread,))
        assert await repo.load_items(thread) == ()
        assert await repo.load_display_items_page(thread, limit=100) == page
        assert (await repo.load_display_snapshot(thread)).items == page.items
        fresh = UserMessageItem("after model pruning", "next")
        await repo.append_items(thread, (fresh,))
        assert (await repo.load_display_items_page(thread, limit=100)).items == (*page.items, fresh)
        with repo._connect() as db:
            db.execute("DELETE FROM chat_history WHERE id=?", (fresh.id,))
        assert (await repo.load_items(thread)) == (fresh,)

    asyncio.run(run())


def test_projection_rolls_back_with_append_failure(tmp_path):
    async def run():
        repo = SQLiteSessionRepository(tmp_path / "atomic.db")
        thread = ThreadId("fixture")
        await repo.create_thread(thread, tmp_path)
        question = UserMessageItem("original", "turn")
        await repo.append_items(thread, (question,))
        with pytest.raises(StorageIntegrityError):
            await repo.append_items(
                thread,
                (
                    CompactionItem("summary", None, "compact", replacement_item_count=1),
                    replace(question, content="collision"),
                ),
            )
        fresh = UserMessageItem("fresh", "next")
        await repo.append_items(thread, (fresh,))
        assert (await repo.load_display_items_page(thread)).items == (question, fresh)

    asyncio.run(run())


def test_failed_migration_leaves_no_partial_index(tmp_path):
    repo = SQLiteSessionRepository(tmp_path / "invalid.db")
    with repo._connect() as db:
        legacy(db)
        db.execute("INSERT INTO threads(id,cwd) VALUES('fixture','fixture')")
        db.execute(
            "INSERT INTO conversation_items "
            "VALUES('bad','fixture','turn',0,'compaction',?, 'fixture')",
            (json.dumps({"replacement_item_count": -1}),),
        )
    with repo._connect() as db:
        with pytest.raises(ValueError):
            install_chat_history(db)
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='chat_history'").fetchone()
        assert not db.execute("SELECT 1 FROM schema_migrations WHERE name=?", (VERSION,)).fetchone()
        assert db.execute("SELECT count(*) FROM conversation_items").fetchone()[0] == 1
