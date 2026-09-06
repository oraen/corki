import asyncio
from pathlib import Path

from corki.memory import ConversationMemory
from corki.protocol.ids import new_thread_id, new_turn_id
from corki.protocol.items import AssistantMessageItem, ReasoningItem, UserMessageItem, new_step_id
from corki.storage import SQLiteSessionRepository


def test_sqlite_round_trip_and_continuous_memory(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = SQLiteSessionRepository(tmp_path / "corki.db")
        thread_id = new_thread_id()
        await repository.create_thread(thread_id, tmp_path)
        first = new_turn_id()
        second = new_turn_id()
        step = new_step_id()
        items = (
            UserMessageItem("remember the deployment uses kubernetes", first),
            ReasoningItem("provider replay state", first, step),
            AssistantMessageItem("noted", first, step),
            UserMessageItem("unrelated recent", second),
            AssistantMessageItem("ok", second, new_step_id()),
        )
        await repository.append_items(thread_id, items)

        recalled = await ConversationMemory(repository).current(thread_id)

        assert recalled == items
        assert await repository.load_items(thread_id) == items
        await repository.close()

    asyncio.run(scenario())
