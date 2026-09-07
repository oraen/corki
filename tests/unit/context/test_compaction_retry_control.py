import asyncio

import pytest

from corki.context import ContextSnapshot, ContextWindowManager
from corki.models import ModelError, ModelErrorKind, ModelTextDelta
from corki.protocol.ids import new_thread_id, new_turn_id
from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id
from corki.storage import SQLiteSessionRepository


@pytest.mark.parametrize("limit", [0, 1, 5, 500])
def test_local_retry_budget_is_bounded_and_does_not_require_retryable_flag(tmp_path, limit):
    async def scenario():
        calls, closed, notices = [], [], []

        class Model:
            async def stream(self, request):
                calls.append(request)
                try:
                    raise ModelError(
                        "protocol fixture", kind=ModelErrorKind.PROTOCOL, retryable=False
                    )
                    yield
                finally:
                    closed.append(request)

        async def retry(event):
            notices.append(event)

        manager = ContextWindowManager(
            repository=SQLiteSessionRepository(tmp_path / "sessions.db"),
            model=Model(),
            model_name="fixture",
            context_window_tokens=8000,
            max_retries=limit,
            retry_base_seconds=0,
        )
        with pytest.raises(ModelError, match="protocol fixture"):
            await manager._summarize((), instructions="base", turn_id=new_turn_id(), on_retry=retry)
        assert len(calls) == min(limit, 100) + 1
        assert closed == calls
        assert [e.attempt for e in notices] == list(range(1, min(limit, 100) + 1))
        assert all(e.delay_seconds == 0 for e in notices)

    asyncio.run(scenario())


def test_cancel_during_compaction_backoff_closes_stream_and_preserves_history(tmp_path):
    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "sessions.db")
        thread, old, turn = new_thread_id(), new_turn_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        original = (AssistantMessageItem("original " * 1000, old, new_step_id()),)
        await repository.append_items(thread, original)
        waiting = asyncio.Event()
        calls, closed = [], []

        class Model:
            async def stream(self, request):
                calls.append(request)
                try:
                    yield ModelTextDelta("partial summary")
                    raise ModelError("stream fixture", kind=ModelErrorKind.TRANSPORT)
                finally:
                    closed.append(request)

        async def retry(event):
            waiting.set()

        manager = ContextWindowManager(
            repository=repository,
            model=Model(),
            model_name="fixture",
            context_window_tokens=8000,
            auto_compact_tokens=500,
            retry_base_seconds=60,
        )
        task = asyncio.create_task(
            manager.prepare(
                thread_id=thread,
                turn_id=turn,
                snapshot=ContextSnapshot("base", (), tmp_path),
                tools=(),
                pending_items=(UserMessageItem("current", turn),),
                on_retry=retry,
            )
        )
        try:
            await asyncio.wait_for(waiting.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert len(calls) == 1 and closed == calls
            assert await repository.load_items(thread) == original
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
