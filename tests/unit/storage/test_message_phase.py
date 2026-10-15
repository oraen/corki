"""Optional message phase remains compatible with existing history rows."""

import asyncio
import json
import sqlite3

from corki.protocol.ids import new_thread_id, new_turn_id
from corki.protocol.items import AssistantMessageItem, new_step_id
from corki.storage import SQLiteSessionRepository


def test_old_message_without_phase_can_be_idempotently_appended(tmp_path):
    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "history.db")
        thread, turn = new_thread_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        item = AssistantMessageItem("old", turn, new_step_id())
        await repository.append_items(thread, (item,))
        with sqlite3.connect(repository.path) as connection:
            payload = json.loads(
                connection.execute("SELECT payload_json FROM conversation_items").fetchone()[0]
            )
            payload.pop("phase")
            connection.execute(
                "UPDATE conversation_items SET payload_json=?", (json.dumps(payload),)
            )
        repository = SQLiteSessionRepository(repository.path)
        assert await repository.load_items(thread) == (item,)
        await repository.append_items(thread, (item,))
        assert await repository.load_items(thread) == (item,)

    asyncio.run(scenario())
