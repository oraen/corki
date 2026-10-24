"""Real custom-tool subprocess cleanup on both supported desktop platforms."""

import asyncio
import os
import sys

import pytest

from corki.tools import custom


@pytest.mark.parametrize("failure", ["overflow", "cancel"])
def test_custom_output_failure_joins_child(tmp_path, monkeypatch, failure):
    async def scenario():
        children = []
        original = custom.create_subprocess_exec

        async def capture(*args, **kwargs):
            child = await original(*args, **kwargs)
            children.append(child)
            return child

        monkeypatch.setattr(custom, "create_subprocess_exec", capture)
        marker = tmp_path / "ready"
        script = (
            "import pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text('ready'); "
            + ("sys.stdout.write('x'*4000000); sys.stdout.flush(); "
               if failure == "overflow" else "")
            + "time.sleep(120)"
        )
        task = asyncio.create_task(custom.run_process(
            [sys.executable, "-X", "utf8", "-u", "-c", script, str(marker)],
            {}, tmp_path, dict(os.environ), 90,
        ))
        try:
            async with asyncio.timeout(60):
                while not marker.exists():
                    if task.done():
                        await task
                    await asyncio.sleep(.05)
            if failure == "cancel":
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
            with pytest.raises(ValueError if failure == "overflow" else asyncio.CancelledError):
                await asyncio.wait_for(task, 30)
            assert len(children) == 1 and children[0].returncode is not None
            if os.name == "nt":
                assert not children[0]._corki_job_finalizer.alive
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["timeout", "cancel"])
def test_custom_owns_pending_spawn_until_cleanup(tmp_path, monkeypatch, failure):
    async def scenario():
        created, release = asyncio.Event(), asyncio.Event()
        children = []
        original = custom.create_subprocess_exec

        async def held(*args, **kwargs):
            child = await original(*args, **kwargs)
            children.append(child)
            created.set()
            await release.wait()
            return child

        monkeypatch.setattr(custom, "create_subprocess_exec", held)
        task = asyncio.create_task(custom.run_process(
            [sys.executable, "-c", "import time;time.sleep(120)"],
            {}, tmp_path, dict(os.environ), .1 if failure == "timeout" else 90,
        ))
        try:
            await asyncio.wait_for(created.wait(), 60)
            if failure == "cancel":
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
            else:
                await asyncio.sleep(.2)
            assert not task.done()  # Ownership must survive timeout/cancellation.
            release.set()
            with pytest.raises(TimeoutError if failure == "timeout" else asyncio.CancelledError):
                await asyncio.wait_for(task, 30)
            assert len(children) == 1 and children[0].returncode is not None
            if os.name == "nt":
                assert not children[0]._corki_job_finalizer.alive
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
