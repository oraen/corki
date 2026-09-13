import asyncio
from types import SimpleNamespace

import pytest
from prompt_toolkit.input.vt100_parser import Vt100Parser

from corki.cli.terminal_palette import TerminalPalette
from corki.cli.terminal_responses import TerminalResponseParser


@pytest.mark.parametrize("mode", ["light", "dark", "missing", "late", "error", "cancel"])
def test_probe_snapshot_and_listener_lifetime(mode):
    async def scenario():
        parser = TerminalResponseParser(Vt100Parser(lambda key: pytest.fail(str(key))))
        palette = TerminalPalette()
        writes = []
        invalidations = []

        def write(text):
            writes.append(text)
            if mode == "error":
                raise OSError("optional terminal probe failed")
            parser.feed("\x1b]10;rgb:00/00/00\x07")
            if mode in {"light", "dark"}:
                rgb = "ff/ff/ff" if mode == "light" else "00/00/00"
                parser.feed("\x1b]11;rgb:" + rgb + "\x1b\\")

        app = SimpleNamespace(
            output=SimpleNamespace(write_raw=write, flush=lambda: None),
            invalidate=lambda: invalidations.append(True),
        )
        task = asyncio.create_task(palette._sample(app, parser))
        if mode == "cancel":
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await asyncio.wait_for(task, 1)
        assert not parser.color_listeners
        if mode == "late":
            parser.feed("\x1b]11;rgb:ff/ff/ff\x07")
        assert writes == ["\x1b]10;?\x1b\\\x1b]11;?\x1b\\"]
        if mode in {"light", "dark"}:
            assert palette.foreground == (0, 0, 0)
            assert palette.background == ((255, 255, 255) if mode == "light" else (0, 0, 0))
            assert invalidations == [True]
        else:
            assert palette.foreground is palette.background is None
            assert invalidations == []
        assert palette.light == (mode == "light")

    asyncio.run(scenario())


def test_non_terminal_probe_is_attempted_once_without_io():
    palette = TerminalPalette()
    app = SimpleNamespace(input=object(), output=object())
    palette._start(app)
    assert palette.attempted
    palette._start(object())
    assert palette.background is None
