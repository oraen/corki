"""Adding personality preserves legacy defaults and canonical history."""

import asyncio
import sqlite3
from dataclasses import replace

import pytest

from corki.protocol.ids import new_thread_id, new_turn_id
from corki.protocol.items import UserMessageItem
from corki.protocol.settings import ThreadModelSettings
from corki.storage import SQLiteSessionRepository


@pytest.mark.parametrize("personality", [None, "none", "friendly", "pragmatic"])
def test_legacy_personality_column_migration_and_reopen(tmp_path, personality):
    async def scenario():
        database = tmp_path / "history.db"
        original = SQLiteSessionRepository(database)
        thread = new_thread_id()
        await original.create_thread(thread, tmp_path)
        history = (UserMessageItem("retain this", new_turn_id()),)
        await original.append_items(thread, history)
        settings = ThreadModelSettings("model", "independent", "high", "plan", "HOST MODE")
        await original.save_thread_model_settings(thread, settings)
        # Only the isolated fixture is downgraded to the pre-personality schema.
        with sqlite3.connect(database) as db:
            db.execute("ALTER TABLE thread_model_settings DROP COLUMN personality")
        migrated = SQLiteSessionRepository(database)
        assert await migrated.load_thread_model_settings(thread) == settings
        assert await migrated.load_items(thread) == history
        selected = replace(settings, personality=personality)
        await migrated.save_thread_model_settings(thread, selected)
        reopened = SQLiteSessionRepository(database)
        assert await reopened.load_thread_model_settings(thread) == selected
        assert await reopened.load_items(thread) == history

    asyncio.run(scenario())
