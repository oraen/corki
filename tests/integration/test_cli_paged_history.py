"""The real CLI hydrates recent history and prepends older pages without sampling."""

import asyncio
from io import StringIO

import pytest
from rich.console import Console

from corki.cli.application import CorkiApplication
from corki.cli.history_pager import HistoryPager
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.protocol.ids import new_turn_id
from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id
from corki.sessions import TurnRecord, TurnStatus


@pytest.mark.parametrize("failure_site", ["read", "replay", "layout"])
def test_cli_loads_older_pages_preserving_live_output_position_and_empty_failure(
    tmp_path, monkeypatch, failure_site
):
    async def scenario():
        class Model:
            async def stream(self, request):
                raise AssertionError("history browsing must not sample")
                yield

            async def aclose(self):
                pass

        settings = CorkiSettings(tmp_path, skills_enabled=False, plugins_enabled=False)
        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            database_path=tmp_path / "history.db",
            model=Model(),
            home_path=tmp_path,
        )
        await runtime._ensure_ready()
        for index in range(150):
            turn = new_turn_id()
            await runtime._repository.save_turn(
                TurnRecord(turn, runtime.thread_id, TurnStatus.COMPLETED, f"USER_{index:03}")
            )
            await runtime._repository.append_items(
                runtime.thread_id,
                (
                    UserMessageItem(f"USER_{index:03}", turn),
                    AssistantMessageItem(f"ANSWER_{index:03}", turn, new_step_id()),
                ),
            )
            if index == 50:
                await runtime._repository.save_turn(
                    TurnRecord(
                        new_turn_id(),
                        runtime.thread_id,
                        TurnStatus.FAILED,
                        "",
                        error="EMPTY_FAILURE",
                    )
                )

        async def forbidden():
            raise AssertionError("CLI must not load the full display snapshot")

        monkeypatch.setattr(runtime, "load_display_snapshot", forbidden)
        monkeypatch.setattr(runtime, "load_display_history", forbidden)
        output = StringIO()

        class UI(TerminalUI):
            async def read_message(self):
                view = self._history_view
                pager = view.loader
                initial = self._transcript.render(80)
                assert "USER_000" not in initial and "USER_149" in initial
                assert len(pager.items) == 100 and pager.has_older
                self.show_notice("LIVE_KEPT")
                view.open()
                view.text()
                row = next(
                    index
                    for index, line in enumerate(view.lines)
                    if "USER_100" in "".join(text for _, text, *_ in line)
                )
                view.row = row
                before = view.lines[row]
                original = runtime.load_display_items_page
                failed = False

                async def fail_once(**kwargs):
                    nonlocal failed
                    if not failed:
                        failed = True
                        raise ValueError("fixture page failure")
                    return await original(**kwargs)

                if failure_site == "read":
                    monkeypatch.setattr(runtime, "load_display_items_page", fail_once)
                else:
                    target = type(self) if failure_site == "replay" else type(view)
                    method = "replay_history" if failure_site == "replay" else "text"
                    original_render = getattr(target, method)
                    attempts = 0

                    def fail_render(instance, *args, **kwargs):
                        nonlocal attempts
                        attempts += 1
                        # Layout is first measured for the old frame, then the new frame.
                        if attempts == (1 if failure_site == "replay" else 2):
                            raise ValueError("fixture page failure")
                        return original_render(instance, *args, **kwargs)

                    monkeypatch.setattr(target, method, fail_render)
                checkpoint = (pager.items, pager.turns, pager.item_cursor, pager.turn_cursor)
                source_before = tuple(self._transcript.calls)
                seen_before = (set(pager.item_seen), set(pager.turn_seen))
                pager.request_older()
                await pager.task
                assert (
                    pager.items,
                    pager.turns,
                    pager.item_cursor,
                    pager.turn_cursor,
                ) == checkpoint
                assert tuple(self._transcript.calls[: len(source_before)]) == source_before
                assert (pager.item_seen, pager.turn_seen) == seen_before
                assert "fixture page failure" in self._transcript.render(80)
                pager.request_older()
                await pager.task
                assert view.lines[view.row] == before
                assert "EMPTY_FAILURE" in self._transcript.render(80)
                pager.request_older(beginning=True)
                await pager.task
                assert not pager.has_older and view.row == 0
                all_history = self._transcript.render(80)
                for index in range(150):
                    assert all_history.count(f"USER_{index:03}") == 1
                    assert all_history.count(f"ANSWER_{index:03}") == 1
                assert all_history.count("EMPTY_FAILURE") == 1
                assert all_history.count("LIVE_KEPT") == 1
                assert (
                    all_history.index("ANSWER_050")
                    < all_history.index("EMPTY_FAILURE")
                    < all_history.index("USER_051")
                )
                assert "USER_000" not in output.getvalue()
                view.close()
                raise EOFError

        ui = UI(settings, tmp_path / "input-history", console=Console(file=output, width=80))
        assert (
            await CorkiApplication(settings, CorkiPaths.from_home(tmp_path), runtime, ui).run() == 0
        )
        assert ui._history_view.loader.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("clear_first", [False, True])
def test_closing_pager_joins_read_and_does_not_apply_cancelled_page(
    tmp_path, monkeypatch, clear_first
):
    async def scenario():
        class Model:
            async def stream(self, request):
                raise AssertionError("display must not sample")
                yield

            async def aclose(self):
                pass

        settings = CorkiSettings(tmp_path, skills_enabled=False, plugins_enabled=False)
        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            database_path=tmp_path / "close.db",
            model=Model(),
            home_path=tmp_path,
        )
        await runtime._ensure_ready()
        turn = new_turn_id()
        await runtime._repository.append_items(
            runtime.thread_id, tuple(UserMessageItem(f"entry {i}", turn) for i in range(120))
        )
        ui = TerminalUI(settings, tmp_path / "input-history", console=Console(file=StringIO()))
        ui.show_welcome()
        pager = HistoryPager(runtime, ui)
        await pager.initialize()
        before = tuple(ui._transcript.calls)
        original = runtime._repository.load_display_items_page
        entered, release = asyncio.Event(), asyncio.Event()

        async def held(thread_id, **kwargs):
            entered.set()
            await release.wait()
            return await original(thread_id, **kwargs)

        monkeypatch.setattr(runtime._repository, "load_display_items_page", held)
        pager.request_older()
        closing = None
        try:
            await asyncio.wait_for(entered.wait(), 5)
            if clear_first:
                ui.clear()
                ui.show_notice("NEW_DISPLAY")
                before = tuple(ui._transcript.calls)
                assert pager.closed and not pager.has_older
                assert ui._history_view.loader is None
            closing = asyncio.create_task(pager.aclose())
            done, _ = await asyncio.wait((closing,), timeout=0.1)
            assert not done
            release.set()
            await asyncio.wait_for(closing, 5)
            assert tuple(ui._transcript.calls) == before
            assert pager.closed and pager.task is None
            pager.request_older()
            assert pager.task is None
        finally:
            release.set()
            if closing is not None:
                await closing
            await pager.aclose()
            await runtime.aclose()

    asyncio.run(scenario())
