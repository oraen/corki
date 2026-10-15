"""An observation, sampling retry and terminal failure must keep distinct owners."""

import asyncio

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelError, ModelErrorKind
from corki.protocol.events import ModelRetryScheduled, TurnCancelled, TurnCompleted, TurnFailed
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall, ToolSpec
from corki.tools import ToolRegistry
from corki.tools.errors import FatalToolError


@pytest.mark.parametrize("terminal", ["fatal", "cancel"])
def test_observation_then_sampling_retry_does_not_replay_tool_or_swallow_terminal(
    tmp_path, terminal
):
    async def scenario():
        requests, effects, events = [], [], []
        entered = asyncio.Event()
        first, last = new_tool_call_id(), new_tool_call_id()

        class Tool:
            spec = ToolSpec("probe", "Exercise failure ownership", {"type": "object"})

            async def execute(self, call, context):
                effects.append(call.id)
                if call.id == first:
                    raise ValueError("recoverable observation")
                if terminal == "fatal":
                    raise FatalToolError("fatal dispatch")
                entered.set()
                await asyncio.Future()

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) > 1:
                    results = [item for item in request.items if isinstance(item, ToolResultItem)]
                    assert len(results) == 1 and results[0].is_error
                    assert "recoverable observation" in results[0].content
                if len(requests) == 2:
                    raise ModelError(
                        "retry this sampling only", kind=ModelErrorKind.TRANSPORT, retryable=True
                    )
                assert len(requests) in (1, 3)
                yield ModelCompleted(
                    (
                        ToolCallItem(
                            ToolCall(first if len(requests) == 1 else last, "probe", {}),
                            request.items[-1].turn_id,
                            new_step_id(),
                        ),
                    )
                )

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Tool())
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                plugins_enabled=False,
                model_max_retries=1,
                model_retry_base_seconds=0.001,
            ),
            model=Model(),
            registry=registry,
            database_path=tmp_path / "state.db",
            home_path=tmp_path / "home",
        )

        async def consume():
            async for event in runtime.stream("exercise error classification"):
                events.append(event)

        task = asyncio.create_task(consume())
        try:
            if terminal == "cancel":
                await asyncio.wait_for(entered.wait(), 5)
                await runtime.cancel_active()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 5)
            else:
                await asyncio.wait_for(task, 5)
            assert len(requests) == 3 and effects == [first, last]
            assert requests[1].items == requests[2].items
            assert sum(isinstance(event, ModelRetryScheduled) for event in events) == 1
            terminals = [
                event
                for event in events
                if isinstance(event, (TurnCompleted, TurnFailed, TurnCancelled))
            ]
            assert len(terminals) == 1
            assert isinstance(terminals[0], TurnFailed if terminal == "fatal" else TurnCancelled)
            if terminal == "fatal":
                assert "fatal dispatch" in terminals[0].error
            assert [event async for event in runtime.resume_pending()] == []
            assert len(requests) == 3 and effects == [first, last]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())
