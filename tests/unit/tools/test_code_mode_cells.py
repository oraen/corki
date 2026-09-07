"""Cell observation ownership and bounded subprocess state."""

import asyncio
import re

import pytest

from corki.code_mode.service import CodeModeService
from corki.tools import ToolRegistry

pytestmark = pytest.mark.skipif(not CodeModeService.available(), reason="install corki[code-mode]")


def cell_id(output):
    return re.search(r"cell ID (\S+)", output.content)[1]


def test_single_observer_cancel_does_not_consume_output_or_terminate_cell():
    async def scenario():
        service = CodeModeService(ToolRegistry())
        waiter = None
        try:
            output = await service.execute(
                "c", "yield_control(); await new Promise(()=>{});", 10000, 1000
            )
            identifier = cell_id(output)
            waiter = asyncio.create_task(service.wait(identifier, 10000, 1000))
            await asyncio.sleep(0)
            with pytest.raises(ValueError, match="active observer"):
                await service.wait(identifier, 0, 1000)
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)
            assert service.cells[identifier].status == "running"
            result = await service.wait(identifier, 0, 1000, terminate=True)
            assert "Script terminated" in result.content and not service.cells
        finally:
            await service.aclose()
            if waiter is not None:
                await asyncio.gather(waiter, return_exceptions=True)

    asyncio.run(scenario())


def test_cell_limit_and_termination_discard_uncommitted_session_writes():
    async def scenario():
        service = CodeModeService(ToolRegistry(), max_cells=1)
        try:
            output = await service.execute(
                "c",
                "store('lost',42); text('stored'); yield_control(); await new Promise(()=>{});",
                10000,
                1000,
            )
            assert "stored" in output.content
            with pytest.raises(ValueError, match="active cell limit"):
                await service.execute("other", "text(1)", 10000, 1000)
            await service.wait(cell_id(output), 0, 1000, terminate=True)
            assert (
                "undefined"
                in (await service.execute("next", "text(load('lost'))", 10000, 1000)).content
            )
            assert not service.cells
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_oversize_engine_frame_fails_bounded_and_joins_process():
    async def scenario():
        service = CodeModeService(ToolRegistry())
        try:
            result = await service.execute("c", "text('x'.repeat(5000000));", 10000, 1000)
            assert "Script failed" in result.content and len(result.content) < 4500
            assert not service.cells
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_store_snapshot_is_taken_at_admission_not_late_worker_start(monkeypatch):
    async def scenario():
        service = CodeModeService(ToolRegistry())
        started, release = asyncio.Event(), asyncio.Event()
        original = asyncio.create_subprocess_exec
        calls = 0

        async def delayed(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                await release.wait()
            return await original(*args, **kwargs)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed)
        first = asyncio.create_task(service.execute("first", "text(load('later'));", 10000, 1000))
        try:
            await started.wait()
            await service.execute("second", "store('later',42);", 10000, 1000)
            release.set()
            result = await first
            assert "undefined" in result.content and service.stored == {"later": 42}
        finally:
            release.set()
            await service.aclose()
            await asyncio.gather(first, return_exceptions=True)

    asyncio.run(scenario())
