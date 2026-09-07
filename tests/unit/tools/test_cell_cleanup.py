"""The engine's result does not predate owned cleanup or race its observer."""

import asyncio
from contextlib import suppress

import pytest

from corki.code_mode.cell import Cell
from corki.code_mode.service import CodeModeService
from corki.tools import ToolRegistry


@pytest.mark.skipif(not CodeModeService.available(), reason="install corki[code-mode]")
@pytest.mark.parametrize("cancel", [False, True])
def test_observer_waiting_on_cleanup_reads_its_final_status(monkeypatch, cancel):
    async def scenario():
        reached, release = asyncio.Event(), asyncio.Event()
        original = asyncio.create_subprocess_exec
        service = CodeModeService(ToolRegistry())

        async def spawn(*args, **kwargs):
            process = await original(*args, **kwargs)
            wait = process.wait

            async def held_wait():
                await wait()
                reached.set()
                await release.wait()
                if not cancel:
                    raise OSError("late reap fault")

            monkeypatch.setattr(process, "wait", held_wait)
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        cell = Cell(service, "cell", "c", "text('done');", {})
        service.cells[cell.id] = cell
        observer = None
        terminators = []
        try:
            await asyncio.wait_for(reached.wait(), 3)
            observer = asyncio.create_task(cell.observe(0, 1000))
            await asyncio.sleep(0)
            if cancel:
                terminators = [asyncio.create_task(cell.terminate()) for _ in range(2)]
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                assert not any(task.done() for task in terminators)
            release.set()
            result = await asyncio.wait_for(observer, 3)
            await asyncio.gather(*terminators)
            assert f"Script {'terminated' if cancel else 'failed'}" in result.content
            assert result.is_error is (not cancel)
            assert cell.task.done() and cell.process.returncode is not None
            if cancel:
                assert cell.task.cancelled()
                await service.aclose()
            else:
                with pytest.raises(OSError, match="late reap fault"):
                    await service.aclose()
        finally:
            release.set()
            await asyncio.gather(
                *([observer] if observer is not None else []), *terminators, return_exceptions=True
            )
            with suppress(OSError):
                await service.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("already_exited", [False, True])
def test_kill_failure_still_reaps_closes_and_joins_other_resources(already_exited):
    async def scenario():
        actions = []
        failure = ProcessLookupError("already gone") if already_exited else OSError("kill fault")

        class Pipe:
            def close(self):
                actions.append("pipe")

        class Process:
            returncode = None
            stdin = Pipe()

            def kill(self):
                actions.append("kill")
                raise failure

            async def wait(self):
                actions.append("wait")

        cell = object.__new__(Cell)
        cell.process = Process()
        cell.service = CodeModeService(ToolRegistry())
        cell.tool_tasks = {asyncio.create_task(asyncio.Event().wait())}
        cell.notifications = {asyncio.create_task(asyncio.Event().wait())}
        error = await cell._cleanup()
        assert error is (None if already_exited else failure)
        assert cell.service.cleanup_error is error
        assert actions == ["kill", "wait", "pipe"]
        assert all(task.done() for task in (*cell.tool_tasks, *cell.notifications))

    asyncio.run(scenario())
