"""Canonical source preview is atomic, sticky and independent of model-visible copies."""

import asyncio
import json
import sqlite3
from dataclasses import replace

import pytest

from corki.memory import SQLiteMemoryRepository
from corki.protocol.ids import new_thread_id, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    ContextItem,
    ContextRole,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import AudioAttachment, ImageAttachment, TextContent
from corki.storage import SQLiteSessionRepository
from corki.storage.sqlite import StorageIntegrityError


def preview(database, thread):
    with sqlite3.connect(database) as connection:
        return connection.execute("SELECT preview FROM threads WHERE id=?", (thread,)).fetchone()[0]


@pytest.mark.parametrize(
    "content,parts,attachments,expected",
    [
        ("  request  ", (), (), "request"),
        ("\u2003\u00a0\t", (), (), ""),
        ("\x1c", (), (), "\x1c"),
        ("context\n## My request for Codex: \t actual request ", (), (), "actual request"),
        ("context\n## My request for Codex: \n", (), (), ""),
        ("", (TextContent("request"),), (), "request"),
        ("stale fallback", (TextContent(" \n"),), (), ""),
        ("", (ImageAttachment("fixture:image"),), (), "[Image]"),
        ("", (), (ImageAttachment("fixture:image"),), "[Image]"),
        ("", (AudioAttachment("fixture:audio"),), (), "[Audio]"),
        ("", (AudioAttachment("fixture:audio"), ImageAttachment("fixture:image")), (), "[Image]"),
        ("", (TextContent("caption"), AudioAttachment("fixture:audio")), (), "caption"),
    ],
)
def test_preview_from_accepted_text_and_media(tmp_path, content, parts, attachments, expected):
    async def scenario():
        database = tmp_path / "history.db"
        repository = SQLiteSessionRepository(database)
        thread, turn = new_thread_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        item = UserMessageItem(content, turn, attachments=attachments, content_items=parts)
        await repository.append_items(thread, (item,))
        assert preview(database, thread) == expected
        assert await repository.load_items(thread) == (item,)
        await repository.close()

    asyncio.run(scenario())


def test_context_summary_and_retained_users_do_not_create_source_preview(tmp_path):
    async def scenario():
        database = tmp_path / "history.db"
        repository = SQLiteSessionRepository(database)
        thread, turn = new_thread_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        items = (
            AssistantMessageItem("assistant", turn, new_step_id()),
            ContextItem("external", ContextRole.USER, "untrusted context", turn),
            CompactionItem("summary", None, turn),
            UserMessageItem("retained", turn, retained_from_id="original-id"),
        )
        await repository.append_items(thread, items)
        assert preview(database, thread) == ""
        assert await repository.load_items(thread) == items
        await repository.close()

    asyncio.run(scenario())


def test_preview_commit_rolls_back_with_item_failure_then_is_sticky_on_replay(tmp_path):
    async def scenario():
        database = tmp_path / "history.db"
        repository = SQLiteSessionRepository(database)
        thread, turn = new_thread_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        original = AssistantMessageItem("original", turn, new_step_id())
        await repository.append_items(thread, (original,))
        user = UserMessageItem("first", turn)
        with pytest.raises(StorageIntegrityError):
            await repository.append_items(thread, (user, replace(original, content="conflict")))
        assert preview(database, thread) == ""
        assert await repository.load_items(thread) == (original,)
        await repository.append_items(thread, (user,))
        await repository.append_items(thread, (user, UserMessageItem("later", turn)))
        assert preview(database, thread) == "first"
        assert len(await repository.load_items(thread)) == 3
        await repository.close()

    asyncio.run(scenario())


def test_legacy_preview_backfill_preserves_raw_rows_and_source_version(tmp_path):
    async def scenario():
        database = tmp_path / "history.db"
        repository = SQLiteSessionRepository(database)
        thread, turn = new_thread_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        items = (
            UserMessageItem("retained", turn, retained_from_id="old"),
            UserMessageItem(" \n", turn),
            UserMessageItem("first real", turn),
            UserMessageItem("later real", turn),
        )
        await repository.append_items(thread, items)
        await repository.close()
        with sqlite3.connect(database) as connection:
            connection.execute("ALTER TABLE threads DROP COLUMN preview")
            rows = connection.execute("SELECT * FROM conversation_items").fetchall()
            version = connection.execute("SELECT updated_at FROM threads").fetchone()[0]
        repositories = await asyncio.gather(
            *(asyncio.to_thread(SQLiteSessionRepository, database) for _ in range(4))
        )
        assert preview(database, thread) == "first real"
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT * FROM conversation_items").fetchall() == rows
            assert connection.execute("SELECT updated_at FROM threads").fetchone()[0] == version
        for reopened in repositories:
            await reopened.close()

    asyncio.run(scenario())


def test_failed_backfill_rolls_back_column_and_can_retry(tmp_path):
    async def scenario():
        database = tmp_path / "history.db"
        repository = SQLiteSessionRepository(database)
        thread = new_thread_id()
        await repository.create_thread(thread, tmp_path)
        await repository.append_items(thread, (UserMessageItem("request", new_turn_id()),))
        with sqlite3.connect(database) as connection:
            original = connection.execute("SELECT payload_json FROM conversation_items").fetchone()[
                0
            ]
            connection.execute("ALTER TABLE threads DROP COLUMN preview")
            connection.execute("UPDATE conversation_items SET payload_json='broken JSON'")
            before = connection.execute("SELECT * FROM threads").fetchall()
        with pytest.raises(json.JSONDecodeError):
            SQLiteSessionRepository(database)
        with sqlite3.connect(database) as connection:
            assert "preview" not in {
                row[1] for row in connection.execute("PRAGMA table_info(threads)")
            }
            assert connection.execute("SELECT * FROM threads").fetchall() == before
            connection.execute("UPDATE conversation_items SET payload_json=?", (original,))
        reopened = SQLiteSessionRepository(database)
        assert preview(database, thread) == "request"
        await reopened.close()
        await repository.close()

    asyncio.run(scenario())


def test_no_preview_is_excluded_before_source_scan_limit(tmp_path, monkeypatch):
    async def scenario():
        database = tmp_path / "history.db"
        repository = SQLiteSessionRepository(database)
        eligible, empty = new_thread_id(), new_thread_id()
        await repository.create_thread(eligible, tmp_path)
        await repository.append_items(eligible, (UserMessageItem("evidence", new_turn_id()),))
        await repository.create_thread(empty, tmp_path)
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE threads SET updated_at=datetime('now', '-1 day') WHERE id=?", (eligible,)
            )
        monkeypatch.setattr("corki.memory.extraction.THREAD_SCAN_LIMIT", 1)
        memory = SQLiteMemoryRepository(database)
        claims = await memory.claim_extraction_jobs(
            current_thread_id=new_thread_id(),
            max_age_days=30,
            min_idle_hours=0,
            limit=1,
            lease_seconds=60,
        )
        assert [c.thread_id for c in claims] == [eligible]
        assert await memory.complete_extraction(claims[0], None)
        await repository.close()

    asyncio.run(scenario())
