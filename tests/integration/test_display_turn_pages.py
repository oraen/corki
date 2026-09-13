"""Empty Turns and mutable terminal facts survive bounded cold history reads."""

import asyncio
from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.protocol.ids import new_thread_id, new_turn_id
from corki.sessions import TurnRecord, TurnStatus
from corki.sessions.models import DisplayTurn


@pytest.mark.parametrize("limit", [1, 2, 5])
def test_turn_pages_preserve_empty_turns_across_updates_and_cold_resume(tmp_path, limit):
    async def scenario():
        from corki.sessions.display import DisplayTurnsCursor

        class Model:
            async def stream(self, request):
                raise AssertionError("turn history must not sample")
                yield

            async def aclose(self):
                pass

        def create(thread=None):
            return LangGraphRuntime.create(
                settings=CorkiSettings(tmp_path, skills_enabled=False, plugins_enabled=False),
                database_path=tmp_path / "turn-pages.db",
                model=Model(),
                thread_id=thread,
                home_path=tmp_path,
            )

        runtime = create()
        try:
            assert (await runtime.load_display_turns_page()).turns == ()
            thread = runtime.thread_id
            turns = tuple(
                TurnRecord(
                    new_turn_id(), thread, status, "", error="failure" if index == 1 else None
                )
                for index, status in enumerate(
                    (
                        TurnStatus.RUNNING,
                        TurnStatus.FAILED,
                        TurnStatus.CANCELLED,
                        TurnStatus.COMPLETED,
                    )
                )
            )
            for turn in turns:
                await runtime._repository.save_turn(turn)
            first = await runtime.load_display_turns_page(limit=limit)
            assert len(first.turns) <= limit
            # Update an already created record: this must not move the page boundary.
            updated = replace(turns[0], status=TurnStatus.FAILED, error="late failure")
            await runtime._repository.save_turn(updated)
            await runtime._repository.save_turn(
                TurnRecord(new_turn_id(), thread, TurnStatus.COMPLETED, "new")
            )
            await runtime.aclose()
            runtime = create(thread)
            collected, cursor = first.turns, first.next_cursor
            while cursor is not None:
                page = await runtime.load_display_turns_page(cursor=cursor, limit=limit)
                assert len(page.turns) <= limit
                assert page == await runtime.load_display_turns_page(cursor=cursor, limit=limit)
                collected = page.turns + collected
                cursor = page.next_cursor
            expected = (updated, *turns[1:]) if limit < len(turns) else turns
            assert collected == tuple(DisplayTurn(t.id, t.status, t.error) for t in expected)
            assert (await runtime.load_display_items_page()).items == ()
            for invalid in (
                DisplayTurnsCursor(new_thread_id(), turns[0].id),
                DisplayTurnsCursor(thread, new_turn_id()),
            ):
                with pytest.raises(ValueError, match="cursor"):
                    await runtime.load_display_turns_page(cursor=invalid)
            for invalid in (0, -1, 201, True, "2", 1.5):
                with pytest.raises(ValueError, match="page limit"):
                    await runtime.load_display_turns_page(limit=invalid)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
