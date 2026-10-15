import asyncio
import shutil
import threading

import pytest

from corki.config import CorkiSettings
from corki.memory.agent_shutdown import ConsolidationShutdownError, ConsolidationShutdowns
from corki.memory.pipeline import LongTermMemoryService
from corki.protocol.ids import new_thread_id


@pytest.mark.parametrize("failure", ["none", "model", "repository", "both"])
def test_service_close_is_shared_cancellation_safe_and_preserves_first_error(tmp_path, failure):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        class Model:
            async def aclose(self):
                calls.append("model")
                entered.set()
                await release.wait()
                if failure in {"model", "both"}:
                    raise OSError("model close failure")

        class Repository:
            async def close(self):
                calls.append("repository")
                if failure in {"repository", "both"}:
                    raise OSError("repository close failure")

        service = LongTermMemoryService(
            settings=CorkiSettings(working_directory=tmp_path),
            repository=Repository(),
            model=Model(),
            close_model=True,
            root=tmp_path / "memories",
        )
        first = asyncio.create_task(service.aclose())
        await asyncio.wait_for(entered.wait(), 1)
        second = asyncio.create_task(service.aclose())
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert calls == ["model"] and not second.done()
        release.set()
        if failure == "none":
            await second
            await service.aclose()
        else:
            error = "model" if failure in {"model", "both"} else "repository"
            with pytest.raises(OSError, match=f"{error} close failure"):
                await second
            with pytest.raises(OSError, match=f"{error} close failure"):
                await service.aclose()
        assert calls == ["model", "repository"]
        with pytest.raises(RuntimeError, match="closed"):
            await service.run_once(new_thread_id())
        service.start(new_thread_id())
        assert service._task is None

    asyncio.run(scenario())


def test_cancelled_late_reclaimer_joins_filesystem_removal(tmp_path):
    async def scenario():
        owner = ConsolidationShutdowns()
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        copy = tmp_path / "copy"
        copy.mkdir()

        class Runtime:
            thread_id = "late-reclaim"

            async def aclose(self):
                pass

        def remove():
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
            shutil.rmtree(copy)

        async def cleanup():
            await asyncio.to_thread(remove)

        try:
            with pytest.raises(ConsolidationShutdownError):
                await owner.close(Runtime(), copy, cleanup, timeout=0)
            await asyncio.wait_for(entered.wait(), 1)
            reclaimer = next(iter(owner._workers.values())).reclaiming
            reclaimer.cancel()
            await asyncio.sleep(0)
            reclaimer.cancel()
            remaining = await owner.settle(timeout=0.02)
            assert remaining[0].status == "reclaiming" and copy.exists()
        finally:
            release.set()
            assert not await owner.settle(timeout=1)
        assert not copy.exists() and not reclaimer.cancelled()

    asyncio.run(scenario())


def test_repeated_cancellation_does_not_restart_deadline_or_cancel_retained_close(tmp_path):
    async def scenario():
        owner = ConsolidationShutdowns()
        release = asyncio.Event()
        finished = False

        class Runtime:
            thread_id = "worker"

            async def aclose(self):
                nonlocal finished
                await release.wait()
                finished = True

        copy = tmp_path / "owned-copy"
        copy.mkdir()

        async def cleanup():
            shutil.rmtree(copy)

        task = asyncio.create_task(owner.close(Runtime(), copy, cleanup, timeout=0.03))
        try:
            # Keep cancelling longer than the deadline; each cancellation must not reset it.
            for _ in range(30):
                await asyncio.sleep(0.004)
                if task.done():
                    break
                task.cancel()
            assert task.done() and not finished
            with pytest.raises(ConsolidationShutdownError) as error:
                task.result()
            assert error.value.cancelled
            assert (await owner.settle(timeout=0))[0].status == "closing"
            waiter = asyncio.create_task(owner.settle(timeout=1))
            await asyncio.sleep(0)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert copy.exists() and owner.retained
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            assert not await owner.settle(timeout=1)
        assert finished and not copy.exists()

    asyncio.run(scenario())


def test_unprintable_close_error_remains_inspectable(tmp_path):
    async def scenario():
        owner = ConsolidationShutdowns()

        class BrokenMessageError(RuntimeError):
            def __str__(self):
                raise ValueError("message formatter failed")

        class Runtime:
            thread_id = "bad-error"

            async def aclose(self):
                raise BrokenMessageError()

        async def cleanup():
            pytest.fail("unconfirmed close must not remove its copy")

        with pytest.raises(ConsolidationShutdownError, match="message unavailable"):
            await owner.close(Runtime(), tmp_path, cleanup, timeout=1)
        retained = await owner.settle(timeout=0)
        assert retained[0].status == "failed"
        assert "BrokenMessageError" in retained[0].error

    asyncio.run(scenario())
