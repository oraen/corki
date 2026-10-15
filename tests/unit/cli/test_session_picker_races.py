import asyncio
from types import SimpleNamespace

from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from corki.cli.session_picker import choose_session


def test_cancelled_old_search_cannot_replace_new_result(tmp_path):
    async def scenario(pipe):
        pages = asyncio.Queue()
        release, retired = asyncio.Event(), asyncio.Event()
        reads = []

        class Catalog:
            async def page(self, *, query, **kwargs):
                pages.put_nowait(query)
                if query == "a":
                    try:
                        await asyncio.Future()
                    except asyncio.CancelledError:
                        # Simulate a reader that finishes despite cancellation.
                        await release.wait()
                        retired.set()
                row = SimpleNamespace(
                    thread_id=query or "initial",
                    preview=query or "initial",
                    updated_at="2026-01-01",
                )
                return SimpleNamespace(rows=(row,), has_more=False)

            async def read(self, thread_id):
                reads.append(thread_id)

        catalog = Catalog()
        catalog.archive_store = catalog
        task = asyncio.create_task(
            choose_session(catalog, tmp_path, input=pipe, output=DummyOutput())
        )
        try:
            async with asyncio.timeout(5):
                assert await pages.get() == ""
                pipe.send_text("a")
                assert await pages.get() == "a"
                pipe.send_text("b")
                assert await pages.get() == "ab"
                release.set()
                await retired.wait()
                pipe.send_text("\r")
                assert await task == "ab"
                assert reads == ["ab"]
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    with create_pipe_input() as pipe:
        asyncio.run(scenario(pipe))


def test_archive_double_press_and_exit_join_owned_operation(tmp_path):
    async def scenario(pipe):
        loaded, started, joined = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = []

        class Catalog:
            async def page(self, **kwargs):
                loaded.set()
                row = SimpleNamespace(
                    thread_id="selected", preview="selected", updated_at="2026-01-01"
                )
                return SimpleNamespace(rows=(row,), has_more=False)

            async def archive(self, thread_id):
                calls.append(thread_id)
                started.set()
                try:
                    await asyncio.Future()
                finally:
                    joined.set()

        task = asyncio.create_task(
            choose_session(Catalog(), tmp_path, input=pipe, output=DummyOutput())
        )
        try:
            async with asyncio.timeout(5):
                await loaded.wait()
                pipe.send_text("\x01")
                await started.wait()
                pipe.send_text("\x01\r\x03")
                assert await task is None
                assert calls == ["selected"]
                assert joined.is_set()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    with create_pipe_input() as pipe:
        asyncio.run(scenario(pipe))
