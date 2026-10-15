"""Bounded Runtime history reads preserve canonical and compaction boundaries."""

import asyncio
from dataclasses import replace

import pytest

from corki.cli.history_projection import HistoryProjection
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.protocol.ids import new_item_id, new_thread_id, new_turn_id
from corki.protocol.items import AssistantMessageItem, CompactionItem, UserMessageItem, new_step_id
from corki.sessions.display import DisplayItemsCursor


class NoSampling:
    async def stream(self, request):
        raise AssertionError("display pages must not sample")
        yield

    async def aclose(self):
        pass


@pytest.mark.parametrize("limit", [1, 2, 3, 7])
@pytest.mark.parametrize("gaps", [False, True])
@pytest.mark.parametrize("cold", [False, True])
def test_runtime_pages_bound_decoding_and_preserve_compaction_across_concurrent_append(
    tmp_path, monkeypatch, limit, gaps, cold
):
    async def scenario():
        import corki.storage.display_pages as storage

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(tmp_path, skills_enabled=False, plugins_enabled=False),
            database_path=tmp_path / "pages.db",
            model=NoSampling(),
            home_path=tmp_path,
        )
        try:
            assert (await runtime.load_display_items_page()).items == ()
            first_turn, second_turn = new_turn_id(), new_turn_id()
            user = UserMessageItem("original", first_turn)
            answer = AssistantMessageItem("answer", first_turn, new_step_id())
            fresh = UserMessageItem("fresh", second_turn)
            items = (
                user,
                answer,
                CompactionItem("summary", answer.id, second_turn, replacement_item_count=3),
                replace(user, id=new_item_id(), retained_from_id=user.id),
                replace(answer, id=new_item_id()),
                CompactionItem(
                    "ignored nested marker", None, first_turn, replacement_item_count=20
                ),
                fresh,
            )
            repository = runtime._repository
            await repository.append_items(runtime.thread_id, items)
            if gaps:
                with repository._connect() as connection:
                    connection.execute("UPDATE conversation_items SET sequence=sequence*10")
            original, decoded = storage.item_from_payload, []

            def decode(kind, payload):
                decoded.append(kind)
                return original(kind, payload)

            monkeypatch.setattr(storage, "item_from_payload", decode)
            cursor, raw, projected, pages = None, (), (), 0
            while True:
                decoded.clear()
                page = await runtime.load_display_items_page(cursor=cursor, limit=limit)
                assert len(decoded) == len(page.items) <= limit
                raw = page.items + raw
                visible = HistoryProjection(page.leading_replacements).feed(page.items)
                projected = visible + projected
                if pages == 0:
                    await repository.append_items(
                        runtime.thread_id, (UserMessageItem("concurrent append", second_turn),)
                    )
                    if cold:
                        thread = runtime.thread_id
                        await runtime.aclose()
                        runtime = await LangGraphRuntime.acreate(
                            settings=CorkiSettings(
                                tmp_path, skills_enabled=False, plugins_enabled=False
                            ),
                            database_path=tmp_path / "pages.db",
                            model=NoSampling(),
                            home_path=tmp_path,
                            thread_id=thread,
                        )
                        await runtime._ensure_ready()
                        repository = runtime._repository
                pages += 1
                if page.next_cursor is None:
                    break
                if cursor is not None:
                    assert page.next_cursor.before_sequence < cursor.before_sequence
                cursor = page.next_cursor
            assert raw == items
            assert projected == (user, answer, fresh)
            assert (await repository.load_items(runtime.thread_id))[: len(items)] == items
            newest = await runtime.load_display_items_page(limit=1)
            assert newest.items[0].content == "concurrent append"
            with pytest.raises(ValueError, match="different thread"):
                await runtime.load_display_items_page(cursor=DisplayItemsCursor(new_thread_id(), 1))
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("sequence", [-1, True, 1.5, "2", 2**63])
def test_cursor_rejects_invalid_sequence(sequence):
    with pytest.raises(ValueError, match="cursor sequence"):
        DisplayItemsCursor(new_thread_id(), sequence)


@pytest.mark.parametrize("limit", [0, -1, 201, True, 1.5, "10"])
def test_runtime_rejects_unbounded_or_invalid_page_limits(tmp_path, limit):
    async def scenario():
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(tmp_path, skills_enabled=False, plugins_enabled=False),
            database_path=tmp_path / "limits.db",
            model=NoSampling(),
            home_path=tmp_path,
        )
        try:
            with pytest.raises(ValueError, match="page limit"):
                await runtime.load_display_items_page(limit=limit)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
