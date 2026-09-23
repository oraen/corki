"""Compaction retries are independent from the model/tool loop's sampling budget."""

import asyncio

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelError, ModelErrorKind, ModelTextDelta
from corki.protocol.events import ContextCompacted, ModelRetryScheduled, TurnCompleted, TurnFailed
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize("failure", ["transport", "policy", "missing_terminal", "exhaust", "reset"])
def test_real_loop_retries_compaction_without_replaying_tools(tmp_path, failure):
    async def scenario():
        requests, summaries, executed, closed = [], [], [], []

        class LargeTool:
            spec = ToolSpec("large", "read observation", {})

            async def execute(self, call, context):
                executed.append(call)
                return ToolResult(call.id, call.name, "IMPORTANT FACT\n" + "x" * 16000)

        class Model:
            async def stream(self, request):
                turn, step = request.items[-1].turn_id, new_step_id()
                compact = (
                    isinstance(request.items[-1], UserMessageItem)
                    and "checkpoint compaction" in request.items[-1].content
                )
                try:
                    if compact:
                        summaries.append(request)
                        index = len(summaries)
                        assert request.tools == ()
                        yield ModelTextDelta("unfinished summary must not be published")
                        if failure == "missing_terminal" and index == 1:
                            return
                        if failure == "reset" and index == 2:
                            raise ModelError(
                                "summary window fixture", kind=ModelErrorKind.CONTEXT_WINDOW
                            )
                        if (
                            index == 1
                            or failure == "exhaust"
                            or (failure == "reset" and index == 3)
                        ):
                            raise ModelError(
                                "summary failure fixture",
                                kind=ModelErrorKind.INVALID_REQUEST
                                if failure == "policy"
                                else ModelErrorKind.TRANSPORT,
                                retryable=False,
                                retry_after_seconds=60,
                            )
                        yield ModelCompleted(
                            (
                                AssistantMessageItem("DO NOT CONCAT earlier message", turn, step),
                                AssistantMessageItem("IMPORTANT FACT retained", turn, step),
                            )
                        )
                    else:
                        requests.append(request)
                        if len(requests) == 1:
                            yield ModelCompleted(
                                (
                                    ToolCallItem(
                                        ToolCall(new_tool_call_id(), "large", {}), turn, step
                                    ),
                                )
                            )
                        else:
                            marker = next(i for i in request.items if isinstance(i, CompactionItem))
                            assert marker.summary == "IMPORTANT FACT retained"
                            assert any(
                                isinstance(i, UserMessageItem) and i.content == "CURRENT INPUT"
                                for i in request.items
                            )
                            yield ModelCompleted((AssistantMessageItem("done", turn, step),))
                finally:
                    if compact:
                        closed.append(request)

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(LargeTool())
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                context_window_tokens=8000,
                auto_compact_tokens=3000,
                max_steps=2,
                model_max_retries=1,
                model_retry_base_seconds=0.001,
            ),
            database_path=tmp_path / "sessions.db",
            registry=registry,
            model=Model(),
        )
        try:
            events = [event async for event in runtime.stream("CURRENT INPUT")]
            assert isinstance(events[-1], TurnFailed if failure == "exhaust" else TurnCompleted), (
                events[-1]
            )
            assert len(summaries) == (4 if failure == "reset" else 2)
            assert all(r.harness_managed_retries for r in summaries)
            assert closed == summaries and len(executed) == 1
            notices = [e for e in events if isinstance(e, ModelRetryScheduled)]
            assert len(notices) == (2 if failure == "reset" else 1)
            assert all(
                e.purpose == "compaction"
                and e.attempt == 1
                and e.max_attempts == 1
                and e.delay_seconds < 1
                for e in notices
            )
            history = await runtime._repository.load_items(runtime.thread_id)
            assert sum(isinstance(i, ToolCallItem) for i in history) == 1
            assert sum(isinstance(i, ToolResultItem) for i in history) == 1
            assert sum(isinstance(i, CompactionItem) for i in history) == (failure != "exhaust")
            assert sum(isinstance(e, ContextCompacted) for e in events) == (failure != "exhaust")
            assert len(requests) == (1 if failure == "exhaust" else 2)
            if failure == "reset":
                assert len(summaries[2].items) < len(summaries[1].items)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
