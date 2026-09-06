import asyncio
from pathlib import Path

import pytest

from corki.context import project


class HangingProcess:
    """Small subprocess double that completes only after termination."""

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.started = asyncio.Event()
        self.reaped = asyncio.Event()
        self.terminated = False
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15
        self.reaped.set()

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self.reaped.set()

    async def wait(self) -> int:
        await self.reaped.wait()
        assert self.returncode is not None
        return self.returncode


def test_git_probe_timeout_terminates_and_reaps_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = HangingProcess()

    async def spawn(*args: object, **kwargs: object) -> HangingProcess:
        del args, kwargs
        return process

    monkeypatch.setattr(project.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(project, "_GIT_TIMEOUT_SECONDS", 0.001)

    assert asyncio.run(project._git(tmp_path, "status")) is None
    assert process.terminated
    assert process.reaped.is_set()
    assert not process.killed


def test_git_probe_cancellation_terminates_child_and_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> HangingProcess:
        process = HangingProcess()

        async def spawn(*args: object, **kwargs: object) -> HangingProcess:
            del args, kwargs
            return process

        monkeypatch.setattr(project.asyncio, "create_subprocess_exec", spawn)
        task = asyncio.create_task(project._git(tmp_path, "status"))
        await process.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return process

    process = asyncio.run(scenario())
    assert process.terminated
    assert process.reaped.is_set()
