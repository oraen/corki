"""A startup replay does not own terminal input until an actual Turn starts."""

import asyncio

import pytest

from corki.cli.application import CorkiApplication, _signal_turn_start
from corki.config import CorkiPaths, CorkiSettings
from corki.protocol.events import TurnCompleted, TurnStarted, WarningEvent
from corki.protocol.ids import new_thread_id, new_turn_id


@pytest.mark.parametrize("outcome", ["empty", "error", "active", "cancel"])
def test_resume_reader_starts_only_after_actual_turn_and_is_joined(tmp_path, outcome):
    async def scenario():
        entered, submitted = asyncio.Event(), asyncio.Event()
        closed = False

        class UI:
            reads = 0
            reader_closed = False

            def show_notice(self, text):
                pass

            def show_assistant_message(self, text, **kwargs):
                pass

            async def read_message(self):
                self.reads += 1
                if self.reads == 1:
                    return "next turn"
                submitted.set()
                try:
                    await asyncio.Future()
                finally:
                    self.reader_closed = True

        class Runtime:
            cancelled = 0

            async def cancel_active(self):
                self.cancelled += 1

            async def steer(self, message):
                raise AssertionError("recovered Turn is not realtime")

        ui, runtime = UI(), Runtime()
        app = CorkiApplication(CorkiSettings(tmp_path), CorkiPaths.from_home(tmp_path), runtime, ui)
        thread, turn = new_thread_id(), new_turn_id()

        async def events():
            nonlocal closed
            try:
                yield WarningEvent(thread, turn, "loading")
                await asyncio.sleep(0)
                assert ui.reads == 0
                entered.set()
                if outcome == "cancel":
                    await asyncio.Future()
                if outcome == "error":
                    raise ValueError("bad checkpoint")
                if outcome == "active":
                    yield TurnStarted(thread, turn, resumed=True)
                    await submitted.wait()
                    yield TurnCompleted(thread, turn, "done")
            finally:
                closed = True

        started = asyncio.Event()
        task = asyncio.create_task(
            app._consume_interactive_events(
                _signal_turn_start(events(), started),
                steering_enabled=False,
                started=started,
            )
        )
        await asyncio.wait_for(entered.wait(), 2)
        if outcome == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert runtime.cancelled == 1
        elif outcome == "error":
            with pytest.raises(ValueError, match="bad checkpoint"):
                await task
        else:
            await asyncio.wait_for(task, 2)
        assert closed and app._input._reader is None
        assert ui.reads == (2 if outcome == "active" else 0)
        assert list(app._pending_messages) == (["next turn"] if outcome == "active" else [])
        if outcome == "active":
            assert ui.reader_closed
        assert not [t for t in asyncio.all_tasks() if t.get_name() == "corki-resume-start"]

    asyncio.run(scenario())
