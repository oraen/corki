import asyncio

import pytest

from corki.agents.controller import AgentController
from corki.protocol.events import TurnCompleted


def test_ownership_limits_timeout_and_shutdown():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        closed = []

        class Runtime:
            async def stream(self, message):
                entered.set()
                await release.wait()
                yield TurnCompleted("child", "turn", message)

            async def cancel_active(self):
                pass

            async def aclose(self):
                closed.append(True)

        async def factory(identity, context):
            return Runtime()

        owner = AgentController(factory, limit=1)
        child = await owner.spawn("task", None)
        await entered.wait()
        with pytest.raises(ValueError, match="limit"):
            await owner.spawn("second", None)
        with pytest.raises(ValueError, match="Unknown"):
            await owner.wait(["foreign-id"], 0)
        assert (await owner.wait([child["id"]], 1))["timed_out"]
        await owner.stop_all()
        assert owner.get(child["id"]).status == "cancelled"
        release.set()
        await owner.send(child["id"], "follow up")
        result = await owner.wait([child["id"]], 1000)
        assert result["agents"][0]["result"] == "follow up"
        await owner.aclose()
        assert closed == [True, True]
        assert not owner.children
        with pytest.raises(ValueError, match="closed"):
            await owner.spawn("after close", None)

    asyncio.run(run())


def test_cancel_during_startup_and_failed_factory_retry():
    async def run():
        entered = asyncio.Event()

        async def factory(identity, context):
            assert context == "inherited"
            entered.set()
            await asyncio.Event().wait()

        owner = AgentController(factory)
        await owner.spawn("task", "inherited")
        await entered.wait()
        await owner.aclose()
        assert not owner.children
        assert all(t.done() or t is asyncio.current_task() for t in asyncio.all_tasks())

    asyncio.run(run())


def test_factory_failure_is_terminal_and_retry_keeps_context():
    async def run():
        attempts = []

        async def factory(identity, context):
            attempts.append(context)
            raise RuntimeError("startup unavailable")

        owner = AgentController(factory)
        child = await owner.spawn("task", "policy snapshot")
        result = await owner.wait([child["id"]], 1000)
        assert result["agents"][0]["status"] == "failed"
        assert result["agents"][0]["result"] == "startup unavailable"
        await owner.send(child["id"], "explicit retry")
        await owner.wait([child["id"]], 1000)
        assert attempts == ["policy snapshot", "policy snapshot"]
        await owner.aclose()

    asyncio.run(run())
