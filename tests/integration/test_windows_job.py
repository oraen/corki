"""Real Windows process containment tests; never emulate these with mocks."""

import asyncio
import os
import sys
from pathlib import Path

import pytest

from corki.platforms.processes import close_job, create_subprocess_exec

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires native Windows jobs")


def test_job_stops_descendants_after_leader_exit(tmp_path):
    async def scenario():
        marker = tmp_path / "heartbeat"
        child = (
            "import pathlib,time,sys; p=pathlib.Path(sys.argv[1]); "
            "\nwhile True: p.write_text(str(time.time())); time.sleep(.05)"
        )
        parent = (
            "import subprocess,sys; "
            "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]], "
            "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)"
        )
        unrelated = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import time; time.sleep(60)"
        )
        process = None
        try:
            process = await create_subprocess_exec(
                sys.executable, "-c", parent, child, str(marker)
            )
            await asyncio.wait_for(process.wait(), 10)
            async with asyncio.timeout(10):
                while not marker.exists():
                    await asyncio.sleep(.05)
            assert close_job(process)
            await asyncio.sleep(.3)
            stopped = marker.read_text()
            await asyncio.sleep(.3)
            assert marker.read_text() == stopped
            assert unrelated.returncode is None
            assert close_job(process)  # Idempotent cleanup.
        finally:
            if process is not None:
                close_job(process)
            unrelated.kill()
            await unrelated.wait()

    asyncio.run(scenario())


def test_job_preserves_unicode_io_and_cwd(tmp_path):
    async def scenario():
        directory = tmp_path / "中文 空间"
        directory.mkdir()
        process = await create_subprocess_exec(
            sys.executable, "-X", "utf8", "-c",
            "import os,sys; print(os.getcwd()); print(sys.stdin.read())",
            cwd=directory, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate("你好".encode()), 10)
            lines = output.decode().splitlines()
            assert Path(lines[0]) == directory
            assert lines[1] == "你好"
            assert process.returncode == 0
        finally:
            close_job(process)

    asyncio.run(scenario())


def test_cancel_during_spawn_does_not_run_or_leak_child(tmp_path, monkeypatch):
    async def scenario():
        import corki.platforms.processes as owner

        original = asyncio.create_subprocess_exec
        created, release = asyncio.Event(), asyncio.Event()
        children = []
        marker = tmp_path / "must-not-run"

        async def gated(*args, **kwargs):
            child = await original(*args, **kwargs)
            children.append(child)
            created.set()
            await release.wait()
            return child

        monkeypatch.setattr(owner.asyncio, "create_subprocess_exec", gated)
        task = asyncio.create_task(owner.create_subprocess_exec(
            sys.executable, "-c",
            "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('ran')", str(marker),
        ))
        await asyncio.wait_for(created.wait(), 10)
        task.cancel()
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 10)
        assert children[0].returncode is not None
        assert not marker.exists()

    asyncio.run(scenario())


def test_parent_crash_closes_job_and_stops_child(tmp_path):
    async def scenario():
        marker = tmp_path / "heartbeat"
        heartbeat = (
            "import pathlib,sys,time; p=pathlib.Path(sys.argv[1]); "
            "\nwhile True: p.write_text(str(time.time())); time.sleep(.05)"
        )
        parent_code = """
import asyncio, sys
from corki.platforms.processes import create_subprocess_exec
async def main():
    child = await create_subprocess_exec(sys.executable, '-c', sys.argv[1], sys.argv[2])
    await child.wait()
asyncio.run(main())
"""
        parent = await asyncio.create_subprocess_exec(
            sys.executable, "-c", parent_code, heartbeat, str(marker),
        )
        try:
            async with asyncio.timeout(10):
                while not marker.exists():
                    await asyncio.sleep(.05)
            parent.kill()  # Abrupt owner exit: no Python cleanup can run.
            await asyncio.wait_for(parent.wait(), 10)
            await asyncio.sleep(.3)
            stopped = marker.read_text()
            await asyncio.sleep(.3)
            assert marker.read_text() == stopped
        finally:
            if parent.returncode is None:
                parent.kill()
            await parent.wait()

    asyncio.run(scenario())
