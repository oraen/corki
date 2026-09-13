import asyncio
import os
import sys
from pathlib import Path

import pytest

from corki.config.permissions import ExecutionPermissions
from corki.execution.owned_process import run_owned
from corki.tools.builtin.process import ProcessManager


@pytest.mark.parametrize("failure", ["output", "timeout", "exit", "cancel"])
def test_helper_failure_and_cancel_reap_the_owned_process(tmp_path, monkeypatch, failure):
    async def scenario():
        spawned = []
        ready = asyncio.Event()
        original = asyncio.create_subprocess_exec

        async def spawn(*args, **kwargs):
            child = await original(*args, **kwargs)
            spawned.append(child)
            ready.set()
            return child

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        script = {
            "output": "import sys; sys.stdout.write('x'*5000); sys.stdout.flush()",
            "timeout": "import time; time.sleep(60)",
            "exit": "raise SystemExit(7)",
            "cancel": "import time; time.sleep(60)",
        }[failure]
        task = asyncio.create_task(
            run_owned(
                [sys.executable, "-I", "-c", script],
                b"input",
                cwd=tmp_path,
                output_limit=100,
                timeout=0.1 if failure == "timeout" else 5,
            )
        )
        await ready.wait()
        if failure == "cancel":
            task.cancel()
        expected = asyncio.CancelledError if failure == "cancel" else ValueError
        with pytest.raises(expected):
            await task
        assert len(spawned) == 1 and spawned[0].returncode is not None
        assert not [
            task
            for task in asyncio.all_tasks()
            if task.get_name().startswith("corki-sandbox-helper")
        ]

    asyncio.run(scenario())


def test_preflight_cancel_retains_preparation_cleanup_failure(tmp_path, monkeypatch):
    from corki.tools.builtin import process

    async def scenario():
        entered = asyncio.Event()

        async def prepare(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                raise OSError("preparation cleanup failed")

        monkeypatch.setattr(process, "sandbox_command", prepare)
        manager = ProcessManager()
        task = asyncio.create_task(
            manager.execute(
                "unused",
                cwd=tmp_path,
                yield_seconds=0,
                permissions=ExecutionPermissions(
                    Path(sys.executable), tmp_path, '{"type":"disabled"}'
                ),
            )
        )
        try:
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            _, pending = await asyncio.wait((task,), timeout=1)
            assert not pending
            with pytest.raises(asyncio.CancelledError) as error:
                await task
            assert str(error.value.__cause__) == "preparation cleanup failed"
            assert not manager._sessions and not manager._starting
        finally:
            await manager.terminate_all()

    asyncio.run(scenario())


def test_cancel_during_spawn_joins_late_child(tmp_path, monkeypatch):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        children = []
        original = asyncio.create_subprocess_exec

        async def delayed(*args, **kwargs):
            entered.set()
            await release.wait()
            child = await original(*args, **kwargs)
            children.append(child)
            return child

        monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed)
        task = asyncio.create_task(
            run_owned(
                [sys.executable, "-I", "-c", "import time; time.sleep(60)"],
                b"",
                cwd=tmp_path,
                output_limit=100,
            )
        )
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(children) == 1 and children[0].returncode is not None

    asyncio.run(scenario())


def test_full_duplex_helper_io_is_bounded_without_deadlock(tmp_path):
    data = os.urandom(200_000)
    script = "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"
    result = asyncio.run(
        run_owned(
            [sys.executable, "-I", "-c", script],
            data,
            cwd=tmp_path,
            output_limit=len(data),
        )
    )
    assert result == data


@pytest.mark.parametrize("action", ["cancel", "close", "both"])
def test_cancel_during_policy_preparation_never_spawns_model_command(tmp_path, monkeypatch, action):
    from corki.tools.builtin import process

    async def scenario():
        entered, release, finalized = asyncio.Event(), asyncio.Event(), asyncio.Event()
        launches = []
        original = process._spawn

        async def compile_policy(permissions, argv, cwd, **kwargs):
            entered.set()
            try:
                await release.wait()
                return argv
            finally:
                finalized.set()

        async def spawn(*args, **kwargs):
            result = await original(*args, **kwargs)
            launches.append(result)
            return result

        monkeypatch.setattr(process, "sandbox_command", compile_policy)
        monkeypatch.setattr(process, "_spawn", spawn)
        manager = ProcessManager()
        permissions = ExecutionPermissions(Path(sys.executable), tmp_path, '{"type":"disabled"}')
        task = asyncio.create_task(
            manager.execute(
                "echo not-admitted",
                cwd=tmp_path,
                yield_seconds=0.1,
                permissions=permissions,
            )
        )
        closers = []
        try:
            await entered.wait()
            if action in {"cancel", "both"}:
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
            if action in {"close", "both"}:
                closers = [asyncio.create_task(manager.terminate_all()) for _ in range(2)]
            _, pending = await asyncio.wait([task, *closers], timeout=1)
            assert not pending, "cancellation waited for the policy preparation to be released"
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.gather(*closers)
            assert finalized.is_set()
            assert not launches
            assert not manager._sessions and not manager._starting
        finally:
            release.set()
            await asyncio.gather(task, *closers, return_exceptions=True)
            await manager.terminate_all()

    asyncio.run(scenario())
