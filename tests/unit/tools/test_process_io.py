import asyncio
import errno

import pytest

from corki.tools.builtin import process_io


def test_write_all_retries_partial_interrupted_and_would_block(monkeypatch):
    async def scenario():
        accepted, waits = bytearray(), []
        outcomes = iter([2, errno.EINTR, errno.EAGAIN, 1, 3])

        def write(fd, data):
            assert fd == 123
            outcome = next(outcomes)
            if outcome in (errno.EINTR, errno.EAGAIN):
                raise OSError(outcome, "retry")
            accepted.extend(data[:outcome])
            return outcome

        async def ready(fd, *, writing=False):
            waits.append((fd, writing))

        monkeypatch.setattr(process_io.os, "write", write)
        monkeypatch.setattr(process_io, "_ready", ready)
        await process_io.write_pty(123, b"abcdef")
        assert accepted == b"abcdef" and waits == [(123, True)]

    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", [0, errno.EBADF])
def test_write_zero_and_unexpected_error_are_not_success(monkeypatch, outcome):
    def write(fd, data):
        if outcome:
            raise OSError(outcome, "bad descriptor")
        return 0

    monkeypatch.setattr(process_io.os, "write", write)
    with pytest.raises(OSError):
        asyncio.run(process_io.write_pty(123, b"data"))


@pytest.mark.parametrize("error", [errno.EIO, errno.EBADF])
def test_read_eio_is_eof_but_unexpected_error_is_reported(monkeypatch, error):
    async def ready(fd, *, writing=False):
        pass

    def read(fd, count):
        raise OSError(error, "injected read")

    monkeypatch.setattr(process_io, "_ready", ready)
    monkeypatch.setattr(process_io.os, "read", read)
    if error == errno.EIO:
        assert asyncio.run(process_io.read_pty(123)) == b""
    else:
        with pytest.raises(OSError):
            asyncio.run(process_io.read_pty(123))
