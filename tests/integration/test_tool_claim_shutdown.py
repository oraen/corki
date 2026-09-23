"""Cancellation owns actual model/claim writes, not just their awaiters."""

import asyncio
import threading

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelItemCompleted
from corki.protocol.events import TurnCancelled, TurnCompleted, TurnFailed
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import ToolCallItem, new_step_id
from corki.protocol.tools import ToolCall, ToolSpec
from corki.storage import SQLiteSessionRepository
from corki.tools import ToolRegistry


@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("action", ["cancel", "close"])
@pytest.mark.parametrize("site, streamed", [("claim", False), ("claim", True), ("model", False)])
def test_shutdown_joins_write_before_releasing_storage(
    tmp_path, monkeypatch, committed, action, site, streamed
):
    async def scenario():
        calls = []
        sampled = []

        class Tool:
            spec = ToolSpec("effect", "fixture", {"type": "object"})

            async def execute(self, call, context):
                calls.append(call)
                raise AssertionError("cancelled claim must not dispatch")

        class Model:
            async def stream(self, request):
                item = ToolCallItem(
                    ToolCall(new_tool_call_id(), "effect", {}),
                    request.items[-1].turn_id,
                    new_step_id(),
                )
                sampled.append(item)
                if streamed:
                    yield ModelItemCompleted(item)
                    await asyncio.Event().wait()
                else:
                    yield ModelCompleted((item,))

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Tool())
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, plugins_enabled=False
            ),
            database_path=tmp_path / "claim.db",
            model=Model(),
            registry=registry,
            home_path=tmp_path,
        )
        repository = runtime._repository
        method = "_claim_tool_call" if site == "claim" else "_commit_model_step"
        write = getattr(repository, method)
        close = repository.close
        entered, release, exited = asyncio.Event(), threading.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        close_observations = []

        def gated_write(*args):
            result = write(*args) if committed else None
            loop.call_soon_threadsafe(entered.set)
            try:
                if not release.wait(5):
                    raise TimeoutError("write gate was not released")
                return result if committed else write(*args)
            finally:
                exited.set()

        async def observed_close():
            close_observations.append(exited.is_set())
            await close()

        monkeypatch.setattr(repository, method, gated_write)
        monkeypatch.setattr(repository, "close", observed_close)
        events = []

        async def consume():
            async for event in runtime.stream("Do the operation"):
                events.append(event)

        consumer = asyncio.create_task(consume())
        stopping = None
        try:
            await asyncio.wait_for(entered.wait(), 5)
            stopping = asyncio.create_task(
                runtime.cancel_active() if action == "cancel" else runtime.aclose()
            )
            # cancel_active requests cancellation; the stream owns Turn join.
            if action == "cancel":
                await stopping
            joining = consumer if action == "cancel" else stopping
            done, _ = await asyncio.wait((joining,), timeout=0.1)
            assert not done, "shutdown abandoned the database worker"
            assert calls == [] and close_observations == []
            release.set()
            await asyncio.wait_for(stopping, 5)
            with pytest.raises(asyncio.CancelledError):
                await consumer
            assert exited.is_set() and calls == []
            assert sum(isinstance(event, TurnCancelled) for event in events) == 1
            assert not any(isinstance(event, (TurnCompleted, TurnFailed)) for event in events)
            await runtime.aclose()
            assert close_observations == [True]
            if site == "model":
                assert len(sampled) == 1
                reopened = SQLiteSessionRepository(tmp_path / "claim.db")
                try:
                    history = await reopened.load_items(runtime.thread_id)
                    assert history.count(sampled[0]) == 1
                    assert await reopened.load_model_step(
                        runtime.thread_id, sampled[0].turn_id, 0
                    ) == ModelCompleted((sampled[0],))
                finally:
                    await reopened.close()
        finally:
            release.set()
            consumer.cancel()
            await asyncio.gather(
                consumer, *([stopping] if stopping else []), return_exceptions=True
            )
            await runtime.aclose()

    asyncio.run(scenario())
