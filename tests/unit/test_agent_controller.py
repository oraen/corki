import asyncio

import pytest

from corki.agents.controller import AgentController
from corki.protocol.events import TurnCompleted


def test_ownership_limits_timeout_and_shutdown():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        closed = []

        class Runtime:
            async def stream(self, message, *, realtime):
                assert realtime
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


def test_messages_during_startup_are_delivered_fifo_once():
    from corki.realtime.controller import RealtimeTurnClosedError

    async def run():
        release = asyncio.Event()
        received = []

        class Runtime:
            accepting = False

            async def stream(self, message, *, realtime):
                assert realtime
                received.append(("turn", message))
                self.accepting = True
                yield object()  # A runtime event makes the admitted turn available.
                self.accepting = False
                yield TurnCompleted("child", "turn", "done")

            async def steer(self, message):
                if not self.accepting:
                    raise RealtimeTurnClosedError("closed")
                received.append(("steer", message))

            async def aclose(self):
                pass

        async def factory(identity, context):
            await release.wait()
            return Runtime()

        owner = AgentController(factory)
        try:
            child = await owner.spawn("first", None)
            await owner.send(child["id"], "second")
            await owner.send(child["id"], "third")
            assert owner.get(child["id"]).snapshot()["pending_messages"] == 2
            release.set()
            await owner.wait([child["id"]], 1000)
            assert received == [("turn", "first"), ("steer", "second"), ("steer", "third")]
            assert not owner.get(child["id"]).pending
        finally:
            release.set()
            await owner.aclose()

    asyncio.run(run())


def test_message_at_closed_turn_boundary_starts_once_after_cleanup():
    from corki.realtime.controller import RealtimeTurnClosedError

    async def run():
        closing, cleanup = asyncio.Event(), asyncio.Event()
        received = []

        class Runtime:
            async def stream(self, message, *, realtime):
                received.append(message)
                if message == "first":
                    closing.set()
                    await cleanup.wait()
                yield TurnCompleted("child", "turn", message)

            async def steer(self, message):
                raise RealtimeTurnClosedError("closed")

            async def aclose(self):
                pass

        async def factory(identity, context):
            return Runtime()

        owner = AgentController(factory)
        try:
            child = await owner.spawn("first", None)
            await closing.wait()
            await owner.send(child["id"], "followup")
            assert received == ["first"]
            cleanup.set()
            result = await owner.wait([child["id"]], 1000)
            assert received == ["first", "followup"]
            assert result["agents"][0]["result"] == "followup"
            assert not owner.get(child["id"]).pending
        finally:
            cleanup.set()
            await owner.aclose()

    asyncio.run(run())


def test_handoff_capacity_and_close_do_not_replay_pending_tasks():
    async def run():
        blocked = asyncio.Event()

        async def factory(identity, context):
            await blocked.wait()
            raise AssertionError("closed startup must not continue")

        owner = AgentController(factory)
        child = await owner.spawn("first", None)
        for i in range(64):
            await owner.send(child["id"], str(i))
        with pytest.raises(ValueError, match="full"):
            await owner.send(child["id"], "overflow")
        await owner.close(child["id"])
        assert not owner.children
        with pytest.raises(ValueError, match="Unknown"):
            await owner.send(child["id"], "do not resurrect")
        await owner.aclose()

    asyncio.run(run())


def test_unexpected_steer_failure_is_not_retried_as_new_work():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        turns = []

        class Runtime:
            async def stream(self, message, *, realtime):
                turns.append(message)
                entered.set()
                await release.wait()
                yield TurnCompleted("child", "turn", "done")

            async def steer(self, message):
                raise OSError("submission outcome unknown")

            async def cancel_active(self):
                pass

            async def aclose(self):
                pass

        async def factory(identity, context):
            return Runtime()

        owner = AgentController(factory)
        try:
            child = await owner.spawn("first", None)
            await entered.wait()
            with pytest.raises(OSError, match="unknown"):
                await owner.send(child["id"], "do not replay")
            assert not owner.get(child["id"]).pending
            release.set()
            await owner.wait([child["id"]], 1000)
            assert turns == ["first"]
        finally:
            release.set()
            await owner.aclose()

    asyncio.run(run())


def test_startup_handoff_survives_turn_input_limit_without_duplicate_work():
    from corki.realtime.controller import RealtimeInputLimitError

    async def run():
        release = asyncio.Event()
        turns, steers = [], []

        class Runtime:
            async def stream(self, message, *, realtime):
                turns.append(message)
                yield object()
                yield TurnCompleted("child", "turn", message)

            async def steer(self, message):
                if steers:
                    raise RealtimeInputLimitError("full")
                steers.append(message)

            async def aclose(self):
                pass

        async def factory(identity, context):
            await release.wait()
            return Runtime()

        owner = AgentController(factory)
        try:
            child = await owner.spawn("initial", None)
            await owner.send(child["id"], "first supplement")
            await owner.send(child["id"], "second supplement")
            release.set()
            await owner.wait([child["id"]], 1000)
            assert turns == ["initial", "second supplement"]
            assert steers == ["first supplement"]
            assert not owner.get(child["id"]).pending
        finally:
            release.set()
            await owner.aclose()

    asyncio.run(run())


def test_unknown_startup_handoff_outcome_is_not_replayed_on_next_send():
    async def run():
        release = asyncio.Event()
        turns, attempted = [], []

        class Runtime:
            async def stream(self, message, *, realtime):
                turns.append(message)
                yield object()
                yield TurnCompleted("child", "turn", message)

            async def steer(self, message):
                attempted.append(message)
                raise OSError("submission outcome unknown")

            async def aclose(self):
                pass

        async def factory(identity, context):
            await release.wait()
            return Runtime()

        owner = AgentController(factory)
        try:
            child = await owner.spawn("initial", None)
            await owner.send(child["id"], "uncertain supplement")
            release.set()
            result = await owner.wait([child["id"]], 1000)
            assert result["agents"][0]["status"] == "failed"
            assert "unknown" in result["agents"][0]["result"]
            assert not owner.get(child["id"]).pending
            await owner.send(child["id"], "explicit new request")
            await owner.wait([child["id"]], 1000)
            assert turns == ["initial", "explicit new request"]
            assert attempted == ["uncertain supplement"]
        finally:
            release.set()
            await owner.aclose()

    asyncio.run(run())


def test_send_during_stop_is_rejected_until_cleanup_finishes():
    async def run():
        cleanup, release = asyncio.Event(), asyncio.Event()

        class Runtime:
            async def stream(self, message, *, realtime):
                yield TurnCompleted("child", "turn", message)

            async def aclose(self):
                cleanup.set()
                await release.wait()

        async def factory(identity, context):
            return Runtime()

        owner = AgentController(factory)
        child = await owner.spawn("initial", None)
        await owner.wait([child["id"]], 1000)
        stopping = asyncio.create_task(owner.stop(child["id"]))
        try:
            await cleanup.wait()
            with pytest.raises(ValueError, match="stopping"):
                await owner.send(child["id"], "do not restart during cleanup")
            assert not owner.get(child["id"]).pending
        finally:
            release.set()
            await stopping
            await owner.aclose()

    asyncio.run(run())
