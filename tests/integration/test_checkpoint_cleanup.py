"""Checkpoint cleanup errors stay visible without replacing the primary control flow."""

import asyncio
from contextlib import suppress

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.core import runtime as runtime_module
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.tools import ToolRegistry


@pytest.mark.parametrize("phase", ["setup_error", "cancel", "shutdown"])
@pytest.mark.parametrize("after_close", [False, True])
@pytest.mark.parametrize("fallback_error", [False, True])
def test_checkpoint_cleanup_preserves_primary_and_closes_actual_connection(
    tmp_path, monkeypatch, caplog, phase, after_close, fallback_error
):
    async def scenario():
        reached = asyncio.Event()
        primary = ValueError("primary setup fault")
        cleanup = OSError("checkpoint context cleanup fault")
        fallback = OSError("checkpoint connection fallback fault")
        factory = runtime_module.AsyncSqliteSaver.from_conn_string
        contexts, requests, closed = [], [], []

        class FaultContext:
            def __init__(self, path):
                self.inner = factory(path)
                self.exits = 0
                self.saver = None
                self.close_connection = None
                contexts.append(self)

            async def __aenter__(self):
                self.saver = await self.inner.__aenter__()
                self.close_connection = self.saver.conn.close
                setup = self.saver.setup

                async def prepare():
                    await setup()
                    if phase == "setup_error":
                        raise primary
                    if phase == "cancel":
                        reached.set()
                        await asyncio.Event().wait()

                self.saver.setup = prepare
                return self.saver

            async def __aexit__(self, *error):
                self.exits += 1
                if after_close:
                    await self.inner.__aexit__(*error)
                if fallback_error:

                    async def failing_close():
                        await self.close_connection()
                        raise fallback

                    self.saver.conn.close = failing_close
                raise cleanup

        attempts = 0

        def create_context(path):
            nonlocal attempts
            attempts += 1
            return FaultContext(path) if attempts == 1 else factory(path)

        monkeypatch.setattr(
            runtime_module.AsyncSqliteSaver, "from_conn_string", staticmethod(create_context)
        )

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(())

            async def aclose(self):
                closed.append("model")

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
            model=Model(),
        )
        repository_close = runtime._repository.close

        async def close_repository():
            closed.append("repository")
            await repository_close()

        monkeypatch.setattr(runtime._repository, "close", close_repository)

        async def consume():
            return [event async for event in runtime.stream("first")]

        consumer = asyncio.create_task(consume())
        try:
            if phase == "cancel":
                await asyncio.wait_for(reached.wait(), 3)
                await runtime.cancel_active()
                with pytest.raises(asyncio.CancelledError):
                    await consumer
            elif phase == "setup_error":
                with pytest.raises(ValueError) as caught:
                    await consumer
                assert caught.value is primary
            else:
                assert isinstance((await consumer)[-1], TurnCompleted)
                with pytest.raises(OSError) as caught:
                    await runtime.aclose()
                assert caught.value is cleanup

            (context,) = contexts
            assert context.exits == 1
            assert context.saver.conn._connection is None
            if phase != "shutdown":
                assert "checkpoint context cleanup fault" in caplog.text
                assert runtime._compiled is None and runtime._checkpointer is None
                assert not requests
                retried = [event async for event in runtime.stream("retry")]
                assert isinstance(retried[-1], TurnCompleted)
                assert len(requests) == 1
            if fallback_error:
                assert "checkpoint connection fallback fault" in caplog.text
            for _ in range(2):
                with pytest.raises(OSError) as caught:
                    await runtime.aclose()
                assert caught.value is cleanup
            assert closed == ["model", "repository"]
            assert context.exits == 1
        finally:
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            with suppress(OSError):
                await runtime.aclose()
            # RED runs must not leak fixture connections if the harness drops them.
            for context in contexts:
                if context.close_connection is not None:
                    context.saver.conn.close = context.close_connection
                    await context.close_connection()
                    with suppress(BaseException):
                        await context.inner.__aexit__(None, None, None)

    asyncio.run(scenario())
