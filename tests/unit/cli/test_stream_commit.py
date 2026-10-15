import asyncio
from contextlib import asynccontextmanager, nullcontext
from io import StringIO
from types import SimpleNamespace

import pytest
from rich.console import Console

from corki.cli import stream_commit
from corki.cli.terminal import TerminalUI
from corki.config import CorkiSettings


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("failure", [None, "update", "write", "flush", "redraw"])
def test_stream_commit_owns_update_write_and_redraw_even_when_waiter_cancelled(
    monkeypatch, cancel, failure
):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        log = []
        proxy = StringIO()
        console = Console(file=proxy, force_terminal=True)
        error = OSError(f"fixture {failure} failure")

        def record(stage, value=None):
            log.append((stage, value) if value is not None else stage)
            if stage == failure:
                raise error

        @asynccontextmanager
        async def terminal():
            entered.set()
            await release.wait()
            try:
                yield
            finally:
                record("redraw")

        def append(delta):
            record("update")
            console.print(delta, end="")

        app = SimpleNamespace(
            is_running=True,
            output=SimpleNamespace(
                enable_autowrap=lambda: None,
                write_raw=lambda text: record("write", text),
                flush=lambda: record("flush"),
            ),
        )
        ui = SimpleNamespace(
            _console=console,
            _session=SimpleNamespace(app=app),
            _live_input_enabled=True,
            append_assistant_delta=append,
        )
        monkeypatch.setattr(stream_commit, "StdoutProxy", StringIO)
        monkeypatch.setattr(stream_commit, "set_app", lambda app: nullcontext())
        monkeypatch.setattr(stream_commit, "in_terminal", terminal)
        task = asyncio.create_task(stream_commit.commit_delta(ui, "Before\n"))
        await entered.wait()
        if cancel:
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        assert log == []
        release.set()
        if cancel:
            with pytest.raises(asyncio.CancelledError):
                await task
        elif failure:
            with pytest.raises(OSError) as caught:
                await task
            assert caught.value is error
        else:
            await task
        expected = ["update", ("write", "Before\n"), "flush"]
        if failure in ("update", "write", "flush"):
            expected = expected[: ("update", "write", "flush").index(failure) + 1]
        assert log == [*expected, "redraw"]
        assert proxy.getvalue() == ""

    asyncio.run(scenario())


def test_first_commit_does_not_queue_prefix_before_prompt_starts(tmp_path, monkeypatch):
    async def scenario():
        proxy, written = StringIO(), []
        ui = TerminalUI(
            CorkiSettings(tmp_path),
            tmp_path / "history",
            console=Console(file=proxy, force_terminal=True, width=80),
        )
        monkeypatch.setattr(stream_commit, "StdoutProxy", StringIO)
        monkeypatch.setattr(ui._session.app.output, "write_raw", written.append)
        monkeypatch.setattr(ui._session.app.output, "flush", lambda: None)
        assert not ui._session.app.is_running
        ui.begin_assistant_message()
        await ui.append_assistant_delta_live("Before\n\n| A | B |\n|---|---|\n| row | val |\n")
        assert "Before" in "".join(written) and "row" not in "".join(written)
        assert "row" in ui._table_source.tail
        assert proxy.getvalue() == ""

    asyncio.run(scenario())
