import asyncio
from types import SimpleNamespace

import pytest

from corki.tools.builtin.process import ProcessManager, _ProcessSession
from corki.tools.builtin.process_retention import select_prunable


@pytest.mark.parametrize(
    "exited,locked,expected",
    [
        ([], [], 0),
        ([1], [], 1),
        ([63], [], 0),
        ([], [0], 1),
        ([1], [1], None),
        ([1, 2], [1], 2),
        ([], list(range(64)), None),
    ],
)
def test_source_pruning_policy(exited, locked, expected):
    async def scenario():
        sessions = [
            _ProcessSession(
                str(i), SimpleNamespace(returncode=0 if i in exited else None), 100, last_used=i
            )
            for i in range(64)
        ]
        for i in locked:
            await sessions[i].interaction_lock.acquire()
        chosen = select_prunable(sessions)
        assert (int(chosen.id) if chosen else None) == expected
        assert select_prunable(sessions[:63]) is None

    asyncio.run(scenario())


@pytest.mark.parametrize("remove", [False, True])
def test_poll_lock_serializes_and_revalidates_identity(monkeypatch, remove):
    async def scenario():
        manager = ProcessManager()
        session = _ProcessSession("session", SimpleNamespace(returncode=None), 100, last_used=0)
        manager._sessions[session.id] = session
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def wait(session, seconds):
            calls.append(seconds)
            entered.set()
            await release.wait()

        monkeypatch.setattr(manager, "_wait_session", wait)
        first = asyncio.create_task(manager.write_stdin(session.id, "", yield_seconds=1))
        await entered.wait()
        assert session.interaction_lock.locked() and session.last_used > 0
        second = asyncio.create_task(manager.write_stdin(session.id, "", yield_seconds=2))
        await asyncio.sleep(0)
        assert calls == [1]
        if remove:
            manager._sessions.pop(session.id)
        release.set()
        await first
        if remove:
            with pytest.raises(ValueError, match="unknown or completed"):
                await second
        else:
            await second
            assert calls == [1, 2]
        assert not session.interaction_lock.locked()

    asyncio.run(scenario())


def test_cancelled_poll_releases_interaction_lock(monkeypatch):
    async def scenario():
        manager = ProcessManager()
        session = _ProcessSession("session", SimpleNamespace(returncode=None), 100)
        manager._sessions[session.id] = session
        entered = asyncio.Event()

        async def wait(session, seconds):
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(manager, "_wait_session", wait)
        poll = asyncio.create_task(manager.write_stdin(session.id, "", yield_seconds=1))
        await entered.wait()
        poll.cancel()
        with pytest.raises(asyncio.CancelledError):
            await poll
        assert not session.interaction_lock.locked() and session.id in manager._sessions

    asyncio.run(scenario())


@pytest.mark.parametrize("locked", [False, True])
def test_failed_process_is_logically_exited_before_os_reap(locked):
    async def scenario():
        sessions = [
            _ProcessSession(str(i), SimpleNamespace(returncode=None), 100, last_used=i)
            for i in range(64)
        ]
        sessions[1].failure = OSError("termination failed")
        if locked:
            await sessions[1].interaction_lock.acquire()
        candidate = select_prunable(sessions)
        assert candidate is (None if locked else sessions[1])

    asyncio.run(scenario())
