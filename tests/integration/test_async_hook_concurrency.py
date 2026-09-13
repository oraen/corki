"""The actual Runtime owners share one session-wide asynchronous budget."""

import asyncio

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted


class Model:
    async def stream(self, request):
        yield ModelCompleted(())

    async def aclose(self):
        pass


@pytest.mark.parametrize("close_queued", [False, True])
def test_pre_post_stop_share_session_budget(tmp_path, close_queued):
    async def scenario():
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(tmp_path, skills_enabled=False, plugins_enabled=False),
            model=Model(),
            database_path=tmp_path / "state.db",
            home_path=tmp_path,
        )
        owners = (
            runtime._graph._stop_hooks._async,
            runtime._graph._async_pre_hooks.owner,
            runtime._graph._async_post_hooks.owner,
        )
        release = asyncio.Event()
        started, finished = [], []

        async def execute(key):
            started.append(key)
            try:
                await release.wait()
                return key
            finally:
                finished.append(key)

        try:
            for index in range(9):
                for group, owner in enumerate(owners):
                    key = f"{group}:{index}"
                    owner.start(key, {}, lambda key=key: execute(key))
            # All tasks were created before these scheduling checkpoints.
            for _ in range(20):
                await asyncio.sleep(0)
            assert len(started) == 8
            if close_queued:
                await runtime.aclose()
                assert len(started) == 8
            else:
                release.set()
                async with asyncio.timeout(3):
                    while any(owner._tasks for owner in owners):
                        await asyncio.sleep(0)
                assert len(started) == 27
            assert sorted(finished) == sorted(started)
        finally:
            await runtime.aclose()
        assert all(not owner._tasks for owner in owners)

    asyncio.run(scenario())
