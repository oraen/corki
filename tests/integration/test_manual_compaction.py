import asyncio

import pytest

from corki.config import CorkiSettings
from corki.context import active_history
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelError, ModelErrorKind
from corki.protocol.events import ContextCompacted, ModelRetryScheduled, TurnCompleted, TurnFailed
from corki.protocol.ids import new_tool_call_id, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    ContextItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall, ToolExposure, ToolResult, ToolSpec
from corki.tools import ToolRegistry


def test_manual_compaction_of_empty_history_then_regular_turn(tmp_path):
    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(
                    (
                        AssistantMessageItem(
                            "summary" if len(requests) == 1 else "done",
                            request.items[-1].turn_id,
                            new_step_id(),
                        ),
                    )
                )

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path),
            database_path=tmp_path / "sessions.db",
            home_path=tmp_path / "home",
            model=Model(),
            registry=ToolRegistry(),
        )
        try:
            events = [e async for e in runtime.compact()]
            assert isinstance(events[-1], TurnCompleted) and events[-1].final_answer == ""
            assert any(isinstance(e, ContextCompacted) for e in events)
            assert len(requests) == 1 and requests[0].tools == ()
            assert len(requests[0].items) == 1 and isinstance(requests[0].items[0], UserMessageItem)
            stored = await runtime._repository.load_items(runtime.thread_id)
            assert len(stored) == 1 and isinstance(stored[0], CompactionItem)
            assert stored[0].through_item_id is None
            events = [e async for e in runtime.stream("continue")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(requests) == 2
            assert any(isinstance(i, ContextItem) for i in requests[-1].items)
            assert requests[-1].items[-1].content == "continue"
            assert any(
                isinstance(i, CompactionItem)
                for i in active_history(await runtime._repository.load_items(runtime.thread_id))
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_manual_compaction_twice_resets_discovery_and_rebuilds_context(tmp_path):
    async def scenario():
        custom = "SUMMARIZE THIS HISTORY"
        config = tmp_path / "config.toml"
        config.write_text(f'compact_prompt = "  {custom}  "\n')
        requests, summaries, executions = [], [], []

        class Tool:
            spec = ToolSpec(
                "calendar", "calendar meeting", {"type": "object"}, exposure=ToolExposure.DEFERRED
            )

            async def execute(self, call, context):
                executions.append(call.id)
                return ToolResult(call.id, call.name, "CALENDAR OBSERVATION")

        class Model:
            async def stream(self, request):
                turn, step = request.items[-1].turn_id, new_step_id()
                if (
                    isinstance(request.items[-1], UserMessageItem)
                    and request.items[-1].content == custom
                ):
                    summaries.append(request)
                    assert request.tools == () and request.context_items == ()
                    items = (AssistantMessageItem("SUMMARY", turn, step),)
                else:
                    requests.append(request)
                    index = len(requests)
                    if index == 1:
                        assert "calendar" not in {t.name for t in request.tools}
                        call = ToolCall(
                            new_tool_call_id(), "tool_search", {"query": "calendar meeting"}
                        )
                        items = (ToolCallItem(call, turn, step),)
                    elif index == 2:
                        assert "calendar" in {t.name for t in request.tools}
                        items = (
                            ToolCallItem(ToolCall(new_tool_call_id(), "calendar", {}), turn, step),
                        )
                    else:
                        items = (AssistantMessageItem("done", turn, step),)
                yield ModelCompleted(items)

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Tool())
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings.for_directory(tmp_path, config_file=config),
            database_path=tmp_path / "sessions.db",
            home_path=tmp_path / "home",
            model=Model(),
            registry=registry,
        )
        try:
            assert isinstance(
                [e async for e in runtime.stream("calendar request")][-1], TurnCompleted
            )
            original = await runtime._repository.load_items(runtime.thread_id)
            assert any(
                isinstance(i, ToolResultItem) and i.content == "CALENDAR OBSERVATION"
                for i in original
            )
            for _ in range(2):
                events = [e async for e in runtime.compact()]
                assert isinstance(events[-1], TurnCompleted)
                stored = await runtime._repository.load_items(runtime.thread_id)
                assert stored[: len(original)] == original
                current = active_history(stored)
                assert [type(i) for i in current] == [UserMessageItem, CompactionItem]
                assert current[0].content == "calendar request"
            assert len(summaries) == 2 and len(executions) == 1
            assert all(r.instructions == requests[0].instructions for r in summaries)
            assert isinstance([e async for e in runtime.stream("continue")][-1], TurnCompleted)
            assert "calendar" not in {t.name for t in requests[-1].tools}
            assert "tool_search" in {t.name for t in requests[-1].tools}
            assert any(isinstance(i, ContextItem) for i in requests[-1].items)
            assert len(requests) == 4 and len(executions) == 1
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["authentication", "empty", "exception"])
@pytest.mark.parametrize("has_history", [False, True])
def test_manual_failure_never_installs_a_summary_or_reports_success(tmp_path, failure, has_history):
    async def scenario():
        calls = []

        class Model:
            async def stream(self, request):
                calls.append(request)
                if failure == "authentication":
                    raise ModelError("denied", kind=ModelErrorKind.AUTHENTICATION)
                if failure == "exception":
                    raise RuntimeError("extension failed")
                yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path, model_max_retries=1, model_retry_base_seconds=0.001
            ),
            database_path=tmp_path / "sessions.db",
            model=Model(),
            registry=ToolRegistry(),
            home_path=tmp_path / "home",
        )
        try:
            await runtime._ensure_ready()
            before = (UserMessageItem("preserve this input", new_turn_id()),) if has_history else ()
            await runtime._repository.append_items(runtime.thread_id, before)
            events = [e async for e in runtime.compact()]
            assert isinstance(events[-1], TurnFailed)
            assert not any(isinstance(e, ContextCompacted) for e in events)
            assert await runtime._repository.load_items(runtime.thread_id) == before
            assert len(calls) == (1 if failure == "exception" else 2)
            retries = [e for e in events if isinstance(e, ModelRetryScheduled)]
            assert len(retries) == (0 if failure == "exception" else 1)
            assert all(e.purpose == "compaction" for e in retries)
            assert await runtime._repository.latest_running_turn(runtime.thread_id) is None
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
