import asyncio

from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from corki.cli.session_catalog import SessionCatalog
from corki.cli.session_transcript import page_text, view_session
from corki.protocol.ids import new_thread_id, new_turn_id
from corki.protocol.items import ToolResultItem, UserMessageItem
from corki.sessions.display import DisplayItemsPage
from corki.storage import SQLiteSessionRepository


def test_transcript_pages_preserve_full_content_and_all_history(tmp_path):
    async def scenario():
        repo = SQLiteSessionRepository(tmp_path / "db")
        thread, turn = new_thread_id(), new_turn_id()
        try:
            await repo.create_thread(thread, tmp_path)
            await repo.append_items(thread, tuple(UserMessageItem(str(n), turn) for n in range(45)))
            catalog = SessionCatalog(tmp_path / "db")
            chunks, cursor = [], None
            while True:
                page = await catalog.transcript_page(thread, cursor=cursor)
                assert len(page.items) <= 20
                chunks.insert(0, [item.content for item in page.items])
                cursor = page.next_cursor
                if cursor is None:
                    break
            assert [text for chunk in chunks for text in chunk] == [str(n) for n in range(45)]
        finally:
            await repo.close()

    asyncio.run(scenario())
    content = "x" * 5000 + "END_MARKER"
    assert content in page_text(
        DisplayItemsPage((ToolResultItem("id", "tool", content, "turn"),), None)
    )


def test_loading_cancel_joins_reader():
    async def scenario(pipe):
        entered, joined = asyncio.Event(), asyncio.Event()

        class Catalog:
            async def transcript_page(self, *args, **kwargs):
                entered.set()
                try:
                    await asyncio.Future()
                finally:
                    joined.set()

        task = asyncio.create_task(
            view_session(Catalog(), "thread", input=pipe, output=DummyOutput())
        )
        try:
            async with asyncio.timeout(5):
                await entered.wait()
                pipe.send_text("\x1b")
                await task
                assert joined.is_set()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    with create_pipe_input() as pipe:
        asyncio.run(scenario(pipe))
