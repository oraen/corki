import asyncio
from types import SimpleNamespace

from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from corki.cli.session_catalog import SessionCatalog
from corki.cli.session_picker import choose_session
from corki.protocol.ids import new_thread_id, new_turn_id
from corki.protocol.items import UserMessageItem
from corki.storage import SQLiteSessionRepository


def test_previous_page_loading_blocks_stale_preview_and_joins_on_exit(tmp_path):
    async def scenario(pipe):
        pages = asyncio.Queue()
        joined = asyncio.Event()
        requests, previews = [], []

        class Catalog:
            async def page(self, **kwargs):
                requests.append(kwargs["offset"])
                pages.put_nowait(len(requests))
                if len(requests) == 3:
                    try:
                        await asyncio.Future()
                    finally:
                        joined.set()
                row = SimpleNamespace(thread_id="one", preview="one", updated_at="2026-01-01")
                return SimpleNamespace(rows=(row,), has_more=len(requests) == 1)

            async def preview(self, *args, **kwargs):
                previews.append(args)
                return ()

        task = asyncio.create_task(
            choose_session(Catalog(), tmp_path, input=pipe, output=DummyOutput())
        )
        try:
            async with asyncio.timeout(5):
                assert await pages.get() == 1
                pipe.send_text("\x1b[B")
                assert await pages.get() == 2
                pipe.send_text("\x1b[A")
                assert await pages.get() == 3
                # Old page has one row; pending previous-page selection is 49.
                pipe.send_text("\x05\x1b[B\r")
                await asyncio.sleep(0.1)
                assert not task.done()
                assert previews == []
                assert requests == [0, 50, 0]
                pipe.send_text("\x03")
                assert await task is None
                assert joined.is_set()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    with create_pipe_input() as pipe:
        asyncio.run(scenario(pipe))


def test_catalog_filter_page_preview_and_archive_preserve_history(tmp_path):
    async def scenario():
        database = tmp_path / "sessions.db"
        repository = SQLiteSessionRepository(database)
        ids = []
        try:
            for n in range(3):
                thread = new_thread_id()
                ids.append(thread)
                await repository.create_thread(thread, tmp_path / str(n % 2))
                await repository.append_items(
                    thread, (UserMessageItem(f"中文 request {n}", new_turn_id()),)
                )
            catalog = SessionCatalog(database)
            page = await catalog.page(limit=2)
            assert len(page.rows) == 2 and page.has_more
            assert len((await catalog.page(limit=2, offset=2)).rows) == 1
            assert len((await catalog.page(cwd=tmp_path / "0")).rows) == 2
            assert (await catalog.page(query="request 1")).rows[0].thread_id == ids[1]
            assert not (await catalog.page(query="%' OR 1=1 --")).rows
            before = await repository.load_items(ids[1])
            assert await catalog.preview(ids[1]) == (("user_message", "中文 request 1"),)
            await catalog.archive(ids[1])
            assert not (await catalog.page(query="request 1")).rows
            assert (await catalog.page(archived=True)).rows[0].thread_id == ids[1]
            await catalog.unarchive(ids[1])
            assert await repository.load_items(ids[1]) == before
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_catalog_search_uses_unicode_lowercase_in_preview_and_directory(tmp_path):
    async def scenario():
        database = tmp_path / "sessions.db"
        repository = SQLiteSessionRepository(database)
        thread = new_thread_id()
        try:
            await repository.create_thread(thread, tmp_path / "ÜBUNG")
            await repository.append_items(
                thread, (UserMessageItem("ΑΒΓ 中文 Résumé", new_turn_id()),)
            )
            catalog = SessionCatalog(database)
            for query in ("αβγ", "中文", "résumé", "übung"):
                assert [row.thread_id for row in (await catalog.page(query=query)).rows] == [thread]
            assert not (await catalog.page(query="missing")).rows
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_picker_archive_then_restore_and_select(tmp_path):
    async def scenario(pipe):
        database = tmp_path / "sessions.db"
        repository = SQLiteSessionRepository(database)
        thread = new_thread_id()
        await repository.create_thread(thread, tmp_path)
        await repository.append_items(thread, (UserMessageItem("可恢复的会话", new_turn_id()),))
        await repository.close()
        loaded, archived = asyncio.Event(), asyncio.Event()

        class Catalog(SessionCatalog):
            async def page(self, **kwargs):
                result = await super().page(**kwargs)
                loaded.set()
                return result

            async def archive(self, thread_id):
                result = await super().archive(thread_id)
                archived.set()
                return result

        catalog = Catalog(database)
        task = asyncio.create_task(
            choose_session(catalog, tmp_path, input=pipe, output=DummyOutput())
        )
        try:
            async with asyncio.timeout(5):
                await loaded.wait()
                loaded.clear()
                pipe.send_text("\x01")
                await archived.wait()
                await loaded.wait()
                loaded.clear()
                pipe.send_text("\t\x1b[C")
                await loaded.wait()
                pipe.send_text("\r")
                assert await task == str(thread)
                assert not (await catalog.archive_store.read(thread)).archived_at
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    with create_pipe_input() as pipe:
        asyncio.run(scenario(pipe))


def test_empty_picker_cancel(tmp_path):
    async def scenario(pipe):
        repository = SQLiteSessionRepository(tmp_path / "sessions.db")
        await repository.close()
        pipe.send_text("\x03")
        async with asyncio.timeout(3):
            assert (
                await choose_session(
                    SessionCatalog(tmp_path / "sessions.db"),
                    tmp_path,
                    input=pipe,
                    output=DummyOutput(),
                )
                is None
            )

    with create_pipe_input() as pipe:
        asyncio.run(scenario(pipe))


def test_preview_toggle_cancels_owned_load_without_closing_picker(tmp_path):
    async def scenario(pipe):
        repository = SQLiteSessionRepository(tmp_path / "sessions.db")
        thread = new_thread_id()
        await repository.create_thread(thread, tmp_path)
        await repository.append_items(thread, (UserMessageItem("预览", new_turn_id()),))
        await repository.close()
        loaded, started, joined = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class Catalog(SessionCatalog):
            async def page(self, **kwargs):
                value = await super().page(**kwargs)
                loaded.set()
                return value

            async def preview(self, thread_id, *, limit):
                assert limit == 6
                started.set()
                try:
                    await asyncio.Future()
                finally:
                    joined.set()

        task = asyncio.create_task(
            choose_session(
                Catalog(tmp_path / "sessions.db"),
                tmp_path,
                input=pipe,
                output=DummyOutput(),
            )
        )
        try:
            async with asyncio.timeout(5):
                await loaded.wait()
                pipe.send_text("\x05")
                await started.wait()
                pipe.send_text("\x1b")
                await joined.wait()
                assert not task.done()
                started.clear()
                joined.clear()
                pipe.send_text("\x05")
                await started.wait()
                pipe.send_text("\x05")
                await joined.wait()
                assert not task.done()
                pipe.send_text("\x03")
                assert await task is None
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    with create_pipe_input() as pipe:
        asyncio.run(scenario(pipe))
