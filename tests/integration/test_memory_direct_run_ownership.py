"""The public awaited memory pass must not outlive its owned dependencies."""

import asyncio

import pytest

from corki.config import CorkiSettings
from corki.memory import LongTermMemoryService, SQLiteMemoryRepository
from corki.protocol.ids import new_thread_id
from corki.storage import SQLiteSessionRepository


@pytest.mark.parametrize("count", [1, 2])
@pytest.mark.parametrize("action", ["close", "caller_cancel", "both"])
def test_close_joins_direct_memory_pass_before_closing_dependencies(tmp_path, count, action):
    async def scenario():
        entered, release, settled = asyncio.Event(), asyncio.Event(), asyncio.Event()
        claimed = finished = 0
        closed = []
        database = tmp_path / "state.db"
        sessions = SQLiteSessionRepository(database)

        class Repository(SQLiteMemoryRepository):
            async def claim_extraction_jobs(self, **kwargs):
                nonlocal claimed, finished
                claimed += 1
                if claimed == count:
                    entered.set()
                try:
                    await release.wait()
                    return await super().claim_extraction_jobs(**kwargs)
                finally:
                    finished += 1
                    if finished == count:
                        settled.set()

            async def close(self):
                assert settled.is_set(), "repository closed before direct claim settled"
                closed.append("repository")
                await super().close()

        class Model:
            async def stream(self, request):
                raise AssertionError("a cancelled undispatched pass must not sample")
                yield

            async def aclose(self):
                assert settled.is_set(), "model closed before direct claim settled"
                closed.append("model")

        service = LongTermMemoryService(
            settings=CorkiSettings(tmp_path, memories_enabled=True),
            repository=Repository(database),
            model=Model(),
            root=tmp_path / "memories",
            close_model=True,
        )
        runs = [asyncio.create_task(service.run_once(new_thread_id())) for _ in range(count)]
        close = None
        try:
            await asyncio.wait_for(entered.wait(), 5)
            if action != "close":
                for run in runs:
                    run.cancel()
                await asyncio.sleep(0)
                for run in runs:
                    run.cancel()
            if action != "caller_cancel":
                close = asyncio.create_task(service.aclose())
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(close or runs[0]), 0.05)
            assert closed == []
            release.set()
            if close is not None:
                await asyncio.wait_for(close, 5)
            for run in runs:
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(run, 5)
            assert not service._tasks
            if close is None:
                assert closed == [], "caller cancellation does not close service dependencies"
                await service.aclose()
            assert closed == ["model", "repository"]
            with pytest.raises(RuntimeError, match="closed"):
                await service.run_once(new_thread_id())
        finally:
            release.set()
            for run in runs:
                if not run.done():
                    run.cancel()
            await asyncio.gather(*runs, *([close] if close else []), return_exceptions=True)
            await service.aclose()
            await sessions.close()

    asyncio.run(scenario())
