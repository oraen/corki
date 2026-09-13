"""Explicit host terminal management never targets unrelated OS processes."""

import asyncio
import os

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX child fixtures")


class IdleModel:
    async def stream(self, request):
        yield ModelCompleted(())

    async def aclose(self):
        pass


def create(tmp_path):
    return LangGraphRuntime.create(
        settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
        database_path=tmp_path / "host.db",
        model=IdleModel(),
    )


@pytest.mark.parametrize("tty", [False, True])
def test_host_listing_and_targeted_termination_do_not_affect_sibling(tmp_path, tty):
    async def scenario():
        runtime = create(tmp_path)
        manager = runtime._process_manager
        children = []
        try:
            for index in range(2):
                result = await manager.execute(
                    "exec sleep 60",
                    cwd=tmp_path,
                    login=False,
                    tty=tty,
                    yield_seconds=0.02,
                    item_id=f"call{index}",
                )
                children.append(manager._sessions[result.session_id])
            infos = await runtime.list_background_terminals()
            assert [i.process_id for i in infos] == sorted(child.id for child in children)
            assert {i.item_id for i in infos} == {"call0", "call1"}
            assert all(i.command == "exec sleep 60" and i.cwd == tmp_path for i in infos)
            assert not await runtime.terminate_background_terminal(str(children[0].process.pid))
            assert await runtime.terminate_background_terminal(children[0].id)
            assert not await runtime.terminate_background_terminal(children[0].id)
            assert children[0].cleanup_task.done() and children[0].process.returncode is not None
            assert children[1].process.returncode is None
            assert [i.process_id for i in await runtime.list_background_terminals()] == [
                children[1].id
            ]
            await runtime.clean_background_terminals()
            assert await runtime.list_background_terminals() == () and not manager._sessions
            assert children[1].process.returncode is not None
            # Explicit cleanup is not Runtime closure; it permits new work.
            result = await manager.execute(
                "printf reusable", cwd=tmp_path, login=False, yield_seconds=1
            )
            assert result.exit_code == 0 and result.output == "reusable"
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_exited_unpolled_terminal_hidden_but_explicitly_reapable(tmp_path):
    async def scenario():
        runtime = create(tmp_path)
        manager = runtime._process_manager
        gate = tmp_path / "release"
        try:
            result = await manager.execute(
                f'while test ! -e "{gate}"; do sleep .01; done',
                cwd=tmp_path,
                login=False,
                yield_seconds=0.01,
            )
            child = manager._sessions[result.session_id]
            gate.touch()
            await manager._wait_at_most(child.process, 2)
            assert child.process.returncode == 0
            assert await runtime.list_background_terminals() == ()
            assert await runtime.terminate_background_terminal(child.id)
            assert child.cleanup_task.done() and not manager._sessions
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_failed_single_termination_retains_owned_handle(tmp_path):
    async def scenario():
        runtime = create(tmp_path)
        manager = runtime._process_manager
        stop = manager._stop
        try:
            result = await manager.execute(
                "exec sleep 60", cwd=tmp_path, login=False, yield_seconds=0.01
            )
            child = manager._sessions[result.session_id]

            async def fail(session):
                raise OSError("injected termination failure")

            manager._stop = fail
            assert not await runtime.terminate_background_terminal(child.id)
            assert manager._sessions[child.id] is child and child.process.returncode is None
            assert child.cleanup_task is None
            manager._stop = stop
            assert await runtime.terminate_background_terminal(child.id)
            assert not manager._sessions
        finally:
            manager._stop = stop
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["single", "all", "close"])
def test_management_during_initial_observation_preserves_result(tmp_path, action):
    async def scenario():
        runtime = create(tmp_path)
        manager = runtime._process_manager
        observed = asyncio.Event()
        wait = manager._wait_session

        async def capture(session, seconds):
            observed.set()
            return await wait(session, seconds)

        manager._wait_session = capture
        task = asyncio.create_task(
            manager.execute(
                "printf BEFORE; exec sleep 60",
                cwd=tmp_path,
                login=False,
                yield_seconds=30,
                item_id="active",
            )
        )
        try:
            await asyncio.wait_for(observed.wait(), 3)
            (info,) = await runtime.list_background_terminals()
            child = manager._sessions[info.process_id]
            if action == "single":
                assert await runtime.terminate_background_terminal(info.process_id)
            elif action == "all":
                await runtime.clean_background_terminals()
            else:
                await runtime.aclose()
            result = await asyncio.wait_for(task, 3)
            assert result.session_id is None and result.exit_code is not None
            assert child.cleanup_task.done() and not manager._sessions
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


def test_cancelled_single_termination_joins_cleanup(tmp_path):
    async def scenario():
        runtime = create(tmp_path)
        manager = runtime._process_manager
        stopping, release = asyncio.Event(), asyncio.Event()
        stop = manager._stop
        task = None
        try:
            result = await manager.execute(
                "exec sleep 60", cwd=tmp_path, login=False, yield_seconds=0.01
            )
            child = manager._sessions[result.session_id]

            async def gated(session):
                stopping.set()
                await release.wait()
                await stop(session)

            manager._stop = gated
            task = asyncio.create_task(runtime.terminate_background_terminal(child.id))
            await asyncio.wait_for(stopping.wait(), 3)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
            assert child.process.returncode is not None and child.cleanup_task.done()
            assert not manager._sessions
        finally:
            release.set()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            manager._stop = stop
            await runtime.aclose()

    asyncio.run(scenario())
