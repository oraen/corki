import asyncio
import sqlite3

import pytest

from corki.core import checkpoint_lifecycle


def busy_error(code=sqlite3.SQLITE_BUSY):
    error = sqlite3.OperationalError("fixture SQLite failure")
    error.sqlite_errorcode = code
    return error


@pytest.mark.parametrize("code", [sqlite3.SQLITE_BUSY, sqlite3.SQLITE_BUSY_SNAPSHOT])
def test_bootstrap_busy_retries_same_saver_only(code):
    async def scenario():
        class Saver:
            calls = 0

            async def setup(self):
                self.calls += 1
                if self.calls < 3:
                    raise busy_error(code)

        saver = Saver()
        await checkpoint_lifecycle.setup_checkpoint(saver)
        assert saver.calls == 3

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "error",
    [
        busy_error(sqlite3.SQLITE_LOCKED),
        busy_error(sqlite3.SQLITE_CORRUPT),
        busy_error(sqlite3.SQLITE_IOERR),
        sqlite3.OperationalError("database is locked"),
        OSError("failure"),
        ValueError("invalid setup"),
        asyncio.CancelledError(),
    ],
)
def test_only_numeric_busy_is_retryable(error):
    async def scenario():
        class Saver:
            calls = 0

            async def setup(self):
                self.calls += 1
                raise error

        saver = Saver()
        with pytest.raises(type(error)) as caught:
            await checkpoint_lifecycle.setup_checkpoint(saver)
        assert caught.value is error and saver.calls == 1

    asyncio.run(scenario())


def test_busy_retry_window_does_not_start_a_late_attempt(monkeypatch):
    async def scenario():
        monkeypatch.setattr(checkpoint_lifecycle, "_SETUP_BUSY_RETRY_SECONDS", 0.025)
        error = busy_error()

        class Saver:
            calls = 0

            async def setup(self):
                self.calls += 1
                raise error

        saver = Saver()
        with pytest.raises(sqlite3.OperationalError) as caught:
            await checkpoint_lifecycle.setup_checkpoint(saver)
        assert caught.value is error and 1 <= saver.calls <= 3

    asyncio.run(scenario())


@pytest.mark.parametrize("window,delay", [(0, 0), (0.02, 0.03)])
def test_setup_time_consumes_the_retry_window(monkeypatch, window, delay):
    async def scenario():
        monkeypatch.setattr(checkpoint_lifecycle, "_SETUP_BUSY_RETRY_SECONDS", window)

        class Saver:
            calls = 0

            async def setup(self):
                self.calls += 1
                await asyncio.sleep(delay)
                raise busy_error()

        saver = Saver()
        with pytest.raises(sqlite3.OperationalError):
            await checkpoint_lifecycle.setup_checkpoint(saver)
        assert saver.calls == 1

    asyncio.run(scenario())
