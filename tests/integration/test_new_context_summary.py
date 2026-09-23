"""Explicit window requests summarize once; failure/cancellation do not erase history."""

import asyncio

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelError
from corki.protocol.events import TurnCancelled, TurnCompleted, TurnFailed
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("finish", ["success", "failure", "cancel"])
def test_new_context_summary_failure_and_cold_continuation(tmp_path, mode, finish):
    async def scenario():
        entered = asyncio.Event()
        issued = False
        fail = finish
        summaries = []

        class Model:
            async def stream(self, request):
                nonlocal issued
                turn, step = request.items[-1].turn_id, new_step_id()
                if (
                    isinstance(request.items[-1], UserMessageItem)
                    and request.items[-1].content == "SUMMARY_REQUEST"
                ):
                    summaries.append(request)
                    assert not request.tools
                    assert any(
                        isinstance(i, ToolCallItem) and i.call.name == "new_context"
                        for i in request.items
                    )
                    assert any(
                        isinstance(i, ToolResultItem) and i.state_update.new_context_requested
                        for i in request.items
                    )
                    entered.set()
                    if fail == "failure":
                        raise ModelError("summary fixture failure")
                    if fail == "cancel":
                        await asyncio.Event().wait()
                    item = AssistantMessageItem("SUMMARY_PROOF", turn, step)
                elif not issued:
                    issued = True
                    item = ToolCallItem(ToolCall("window-call", "new_context", {}), turn, step)
                else:
                    assert any(
                        isinstance(i, CompactionItem) and i.summary == "SUMMARY_PROOF"
                        for i in request.items
                    )
                    item = AssistantMessageItem("done", turn, step)
                yield ModelCompleted((item,))

            async def aclose(self):
                pass

        async def create(thread=None):
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    tmp_path,
                    skills_enabled=False,
                    token_budget_enabled=True,
                    tool_mode=mode,
                    compact_prompt="SUMMARY_REQUEST",
                    model_max_retries=0,
                ),
                database_path=tmp_path / "history.db",
                registry=ToolRegistry(),
                model=Model(),
                thread_id=thread,
            )

        runtime = await create()
        task = None
        try:

            async def consume():
                collected = []
                try:
                    async for event in runtime.stream("ORIGINAL_INPUT"):
                        collected.append(event)
                except asyncio.CancelledError:
                    assert finish == "cancel"
                    assert isinstance(collected[-1], TurnCancelled)
                return collected

            task = asyncio.create_task(consume())
            await asyncio.wait_for(entered.wait(), 3)
            if finish == "cancel":
                await runtime.cancel_active()
            events = await asyncio.wait_for(task, 3)
            expected = {"success": TurnCompleted, "failure": TurnFailed, "cancel": TurnCancelled}[
                finish
            ]
            assert isinstance(events[-1], expected), events[-1]
            original = await runtime._repository.load_items(runtime.thread_id)
            assert sum(isinstance(i, CompactionItem) for i in original) == (finish == "success")
            assert any(
                isinstance(i, UserMessageItem) and i.content == "ORIGINAL_INPUT" for i in original
            )
            assert len(summaries) == 1
            thread = runtime.thread_id
            await runtime.aclose()
            fail = "success"
            runtime = await create(thread)
            events = [e async for e in runtime.stream("CONTINUE")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(summaries) == (1 if finish == "success" else 2)
            stored = await runtime._repository.load_items(thread)
            assert stored[: len(original)] == original
            assert (
                sum(isinstance(i, ToolCallItem) and i.call.name == "new_context" for i in stored)
                == 1
            )
            assert (
                sum(isinstance(i, ToolResultItem) and i.tool_name == "new_context" for i in stored)
                == 1
            )
            assert sum(isinstance(i, CompactionItem) for i in stored) == 1
        finally:
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())
