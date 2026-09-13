import asyncio
import threading
from pathlib import Path

import pytest

from corki.core import hook_context
from corki.protocol.ids import ThreadId, new_turn_id
from corki.protocol.items import ContextItem, ContextRole, UserMessageItem
from corki.storage.sqlite import SQLiteSessionRepository


@pytest.mark.parametrize("limit", [0, 20, 2500])
def test_context_cold_reuse_keeps_original_input_owner_and_spill(tmp_path, monkeypatch, limit):
    async def scenario():
        thread, turn = ThreadId("hooks"), new_turn_id()
        admitted = UserMessageItem("admitted", turn)
        pending = UserMessageItem("arrived during hook", turn)
        state = {"thread_id": thread, "turn_id": turn, "request_items": (admitted,)}
        database = tmp_path / "history.db"
        repository = SQLiteSessionRepository(database)
        await repository.create_thread(thread, tmp_path)
        await repository.append_items(thread, (admitted, pending))
        text = "context 中文 " * 2000
        item = await hook_context.prepare_context(repository, state, "hook-key", text, limit)
        await repository.append_items(thread, (item,))
        preview = item.content
        await repository.close()

        def unexpected(*args):
            raise AssertionError("persisted feedback must not be spilled again")

        monkeypatch.setattr(hook_context, "_spill", unexpected)
        cold = SQLiteSessionRepository(database)
        try:
            restored = await hook_context.prepare_context(cold, state, "hook-key", text, limit)
            assert restored == item
            await cold.append_items(thread, (restored,))
            items = [
                item for item in await cold.load_items(thread) if isinstance(item, ContextItem)
            ]
            assert len(items) == 1
            assert items[0].source_input_id == admitted.id
            assert items[0].role == ContextRole.DEVELOPER
            assert items[0].content_kind == "hooks.additional_context"
            if limit:
                path = Path(preview.split("Full hook output saved to: ")[1])
                assert path.read_text() == text
            else:
                assert preview == text
        finally:
            await cold.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("fails", [False, True])
def test_cancel_joins_spill_and_never_appends_feedback(tmp_path, monkeypatch, fails):
    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "history.db")
        thread, turn = ThreadId("cancel"), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        started, release, finished = asyncio.Event(), threading.Event(), threading.Event()
        loop = asyncio.get_running_loop()

        def held(text, limit):
            loop.call_soon_threadsafe(started.set)
            try:
                assert release.wait(5)
                if fails:
                    raise ValueError("worker failure after cancellation")
                return text
            finally:
                finished.set()

        monkeypatch.setattr(hook_context, "_spill", held)
        task = asyncio.create_task(
            hook_context.prepare_context(
                repository,
                {"thread_id": thread, "turn_id": turn, "request_items": ()},
                "cancelled-hook",
                "feedback",
                2500,
            )
        )
        try:
            await asyncio.wait_for(started.wait(), 3)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
            assert finished.is_set()
            assert await repository.load_items(thread) == ()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await repository.close()

    asyncio.run(scenario())
