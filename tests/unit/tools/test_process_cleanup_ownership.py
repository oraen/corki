import asyncio
from types import SimpleNamespace

import pytest

from corki.tools.builtin.process import ProcessManager, _ProcessSession


def test_repeated_cancel_and_concurrent_retirement_join_one_cleanup(monkeypatch):
    async def scenario():
        manager = ProcessManager()
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        session = _ProcessSession("owned", SimpleNamespace(returncode=0), 100)
        session.reader_task = asyncio.create_task(asyncio.Event().wait())
        session.timeout_task = asyncio.create_task(asyncio.Event().wait())
        manager._sessions[session.id] = session

        async def terminate(process):
            calls.append(process)
            entered.set()
            await release.wait()

        monkeypatch.setattr(manager, "_terminate", terminate)
        owner = asyncio.create_task(manager._retire(session))
        await entered.wait()
        other = asyncio.create_task(manager.terminate_all())
        owner.cancel()
        await asyncio.sleep(0)
        owner.cancel()
        assert not session.cleanup_task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await owner
        await other
        assert calls == [session.process]
        assert session.cleanup_task.done() and session.termination_task.done()
        assert session.reader_task.cancelled() and session.timeout_task.cancelled()
        assert not manager._sessions

    asyncio.run(scenario())


def test_reader_failure_is_not_success_and_still_retires_session(monkeypatch):
    async def scenario():
        manager = ProcessManager()
        session = _ProcessSession("owned", SimpleNamespace(returncode=0), 100)
        manager._sessions[session.id] = session

        async def fail():
            raise OSError("reader failed")

        async def terminate(process):
            pass

        monkeypatch.setattr(manager, "_terminate", terminate)
        session.reader_task = asyncio.create_task(fail())
        with pytest.raises(OSError, match="reader failed"):
            await manager._observe(session, 0)
        assert session.cleanup_task.done() and not manager._sessions

    asyncio.run(scenario())


def test_stop_failure_retires_other_sessions_and_is_reported(monkeypatch):
    async def scenario():
        manager = ProcessManager()
        sessions = [_ProcessSession(str(i), SimpleNamespace(returncode=0), 100) for i in range(2)]
        for session in sessions:
            manager._sessions[session.id] = session
            session.reader_task = asyncio.create_task(asyncio.Event().wait())
        calls = []

        async def terminate(process):
            calls.append(process)
            if len(calls) == 1:
                raise OSError("signal denied")

        monkeypatch.setattr(manager, "_terminate", terminate)
        with pytest.raises(OSError, match="signal denied"):
            await manager.terminate_all()
        assert len(calls) == 2 and not manager._sessions
        assert all(session.reader_task.cancelled() for session in sessions)

    asyncio.run(scenario())


def test_cancelled_timeout_waiter_does_not_cancel_shared_termination(monkeypatch):
    async def scenario():
        manager = ProcessManager()
        session = _ProcessSession("owned", SimpleNamespace(returncode=0), 100)
        manager._sessions[session.id] = session
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def terminate(process):
            calls.append(process)
            entered.set()
            await release.wait()

        monkeypatch.setattr(manager, "_terminate", terminate)
        timeout_waiter = asyncio.create_task(manager._stop(session))
        await entered.wait()
        cleanup = asyncio.create_task(manager._retire(session))
        timeout_waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await timeout_waiter
        assert not session.termination_task.done()
        release.set()
        await cleanup
        assert calls == [session.process] and session.termination_task.done()
        assert not manager._sessions

    asyncio.run(scenario())


@pytest.mark.parametrize("stop_fails", [False, True])
def test_first_background_failure_stops_without_poll_and_preserves_cause(monkeypatch, stop_fails):
    async def scenario():
        manager = ProcessManager()
        session = _ProcessSession("failed", SimpleNamespace(returncode=None), 100)
        manager._sessions[session.id] = session
        stopped = asyncio.Event()
        calls = []

        async def terminate(process):
            calls.append(process)
            stopped.set()
            if stop_fails:
                raise OSError("secondary stop failure")
            process.returncode = -15

        async def fail():
            raise OSError("first reader failure")

        monkeypatch.setattr(manager, "_terminate", terminate)
        session.reader_task = asyncio.create_task(fail())
        session.reader_task.add_done_callback(lambda task: manager._record_failure(session, task))
        await asyncio.wait_for(stopped.wait(), 1)
        assert session.failure_event.is_set()
        assert session.id in manager._sessions  # Failure is still retrievable.
        with pytest.raises(OSError, match="first reader failure") as error:
            await manager.write_stdin(session.id, "", yield_seconds=10)
        if stop_fails:
            assert str(error.value.__cause__) == "secondary stop failure"
        assert calls == [session.process] and not manager._sessions

    asyncio.run(scenario())


def test_cancelled_spawn_failure_remains_cancellation_and_closes_admission(tmp_path, monkeypatch):
    from corki.tools.builtin import process as module

    async def scenario():
        manager = ProcessManager()
        entered, release = asyncio.Event(), asyncio.Event()

        async def spawn(*args, **kwargs):
            entered.set()
            await release.wait()
            raise OSError("spawn rejected")

        monkeypatch.setattr(module, "_spawn", spawn)
        call = asyncio.create_task(
            manager.execute("unused", cwd=tmp_path, yield_seconds=0, timeout_seconds=1)
        )
        await entered.wait()
        call.cancel()
        await asyncio.sleep(0)
        close = asyncio.create_task(manager.terminate_all())
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError) as error:
            await call
        assert str(error.value.__cause__) == "spawn rejected"
        await close
        assert not manager._starting and not manager._sessions and not manager._closing

    asyncio.run(scenario())
