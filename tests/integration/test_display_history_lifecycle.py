"""Display reads remain owned across cancellation and concurrent Runtime shutdown."""

import asyncio

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime


@pytest.mark.parametrize("action", ["read", "cancel", "repeat_cancel", "close", "cancel_close"])
@pytest.mark.parametrize("read_fails", [False, True])
@pytest.mark.parametrize("snapshot", [False, True, "page", "turn_page"])
def test_display_read_is_joined_before_resources_close(
    tmp_path, monkeypatch, action, read_fails, snapshot
):
    async def scenario():
        entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
        disposed = []

        class Model:
            async def stream(self, request):
                raise AssertionError("display history must not sample")
                yield

            async def aclose(self):
                disposed.append("model")

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, plugins_enabled=False
            ),
            database_path=tmp_path / "sessions.db",
            model=Model(),
            home_path=tmp_path,
        )
        read = runtime.load_display_snapshot if snapshot else runtime.load_display_history
        if snapshot == "page":
            read = runtime.load_display_items_page
        elif snapshot == "turn_page":
            read = runtime.load_display_turns_page
        await read()
        repository = runtime._repository
        original_close = type(repository).close
        reads = []

        async def held_read(self, thread_id, **kwargs):
            reads.append(thread_id)
            entered.set()
            try:
                await release.wait()
                if read_fails:
                    raise ValueError("display read failed")
                return ()
            finally:
                finished.set()

        async def checked_close(self):
            assert finished.is_set(), "repository closed before its owned read finished"
            disposed.append("repository")
            await original_close(self)

        method = "load_display_snapshot" if snapshot else "load_items"
        if snapshot == "page":
            method = "load_display_items_page"
        elif snapshot == "turn_page":
            method = "load_display_turns_page"
        monkeypatch.setattr(type(repository), method, held_read)
        monkeypatch.setattr(type(repository), "close", checked_close)
        reading = asyncio.create_task(read())
        closing = None
        try:
            await asyncio.wait_for(entered.wait(), 5)
            cancelled = action in {"cancel", "repeat_cancel", "cancel_close"}
            if cancelled:
                reading.cancel()
                await asyncio.sleep(0)
            if action == "repeat_cancel":
                reading.cancel()
                await asyncio.sleep(0)
            if action in {"close", "cancel_close"}:
                closing = asyncio.create_task(runtime.aclose())
                await asyncio.sleep(0)
            assert not reading.done()
            assert not finished.is_set()
            assert disposed == []
            if closing is not None:
                assert not closing.done()
            release.set()
            if cancelled:
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(reading, 5)
            elif read_fails:
                with pytest.raises(ValueError, match="display read failed"):
                    await asyncio.wait_for(reading, 5)
            else:
                assert await asyncio.wait_for(reading, 5) == ()
            assert finished.is_set()
            assert reads == [runtime.thread_id]
        finally:
            release.set()
            await asyncio.gather(reading, return_exceptions=True)
            await asyncio.wait_for(closing if closing is not None else runtime.aclose(), 5)
        assert disposed == ["model", "repository"]
        with pytest.raises(RuntimeError, match="closed"):
            await read()
        assert reads == [runtime.thread_id]

    asyncio.run(scenario())
