"""Default Runtime keeps working past the former fixed step/call ceilings."""

import asyncio

import pytest

from corki.config import CorkiSettings
from corki.context import active_history
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelItemCompleted
from corki.models.types import ModelUsage
from corki.protocol.events import ContextCompacted, TurnCompleted, TurnFailed
from corki.protocol.ids import ToolCallId
from corki.protocol.items import (
    AssistantMessageItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("kind", ["final", "continue", "tool"])
def test_explicit_last_step_allows_final_but_not_followup(tmp_path, streamed, kind):
    async def scenario():
        executed = []

        class Tool:
            spec = ToolSpec("probe", "Count calls", {"type": "object"})

            async def execute(self, call, context):
                executed.append(call.id)
                return ToolResult(call.id, call.name, "ok")

        class Model:
            count = 0
            closed = False

            async def stream(self, request):
                self.count += 1
                turn, step = request.items[-1].turn_id, new_step_id()
                item = (
                    ToolCallItem(ToolCall(ToolCallId("blocked"), "probe", {}), turn, step)
                    if kind == "tool"
                    else AssistantMessageItem("answer", turn, step)
                )
                if streamed:
                    yield ModelItemCompleted(item)
                yield ModelCompleted((item,), end_turn=kind != "continue")

            async def aclose(self):
                self.closed = True

        registry = ToolRegistry()
        registry.register(Tool())
        model = Model()
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path, skills_enabled=False, plugins_enabled=False, max_steps=1
            ),
            registry=registry,
            model=model,
            database_path=tmp_path / "s.db",
            home_path=tmp_path / "home",
        )
        try:
            events = [event async for event in runtime.stream("Finish within the limit")]
            terminal = [e for e in events if isinstance(e, (TurnCompleted, TurnFailed))]
            assert len(terminal) == 1
            if kind == "final":
                assert isinstance(terminal[0], TurnCompleted)
            else:
                assert isinstance(terminal[0], TurnFailed)
                assert "model step limit" in terminal[0].error
            assert model.count == 1
            assert executed == []
            history = await runtime._repository.load_items(runtime.thread_id)
            assert not any(isinstance(item, ToolResultItem) for item in history)
            assert sum(isinstance(item, UserMessageItem) for item in history) == 1
        finally:
            await runtime.aclose()
        assert model.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("nested", [False, True])
def test_default_long_turn_completes_without_implicit_count_limits(tmp_path, streamed, nested):
    async def scenario():
        executed = []

        class Tool:
            spec = ToolSpec("probe", "Count calls", {"type": "object"})

            async def execute(self, call, context):
                executed.append(call.id)
                return ToolResult(call.id, call.name, "ok")

        class Model:
            count = 0

            async def stream(self, request):
                self.count += 1
                turn, step = request.items[-1].turn_id, new_step_id()
                if nested and self.count == 1:
                    items = (
                        ToolCallItem(
                            ToolCall(
                                ToolCallId("cell"),
                                "exec",
                                None,
                                raw_arguments="for(let i=0;i<81;i++) await tools.probe({});",
                                input_kind="freeform",
                            ),
                            turn,
                            step,
                        ),
                    )
                elif not nested and self.count <= 27:
                    items = tuple(
                        ToolCallItem(
                            ToolCall(ToolCallId(f"{self.count}-{n}"), "probe", {}), turn, step
                        )
                        for n in range(3)
                    )
                else:
                    items = (AssistantMessageItem("done", turn, step),)
                if streamed:
                    for item in items:
                        yield ModelItemCompleted(item)
                yield ModelCompleted(items)

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Tool())
        model = Model()
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                plugins_enabled=False,
                tool_mode="code_mode_only" if nested else "direct",
            ),
            registry=registry,
            model=model,
            database_path=tmp_path / "s.db",
            home_path=tmp_path / "home",
        )
        try:
            events = [event async for event in runtime.stream("Finish all work")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert model.count == (2 if nested else 28)
            assert len(executed) == len(set(executed)) == 81
            items = await runtime._repository.load_items(runtime.thread_id)
            assert sum(isinstance(i, ToolResultItem) for i in items) == (1 if nested else 81)
        finally:
            await runtime.aclose()

        class ColdModel:
            count = 0

            async def stream(self, request):
                self.count += 1
                retained_types = (AssistantMessageItem, ToolCallItem, ToolResultItem)
                assert [i for i in request.items if isinstance(i, retained_types)] == [
                    i for i in items if isinstance(i, retained_types)
                ]
                yield ModelCompleted(
                    (AssistantMessageItem("remembered", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        cold_registry = ToolRegistry()
        cold_registry.register(Tool())
        cold_model = ColdModel()
        cold = await LangGraphRuntime.acreate(
            settings=runtime._settings,
            registry=cold_registry,
            model=cold_model,
            database_path=tmp_path / "s.db",
            home_path=tmp_path / "home",
            thread_id=runtime.thread_id,
        )
        try:
            assert [e async for e in cold.resume_pending()] == []
            assert cold_model.count == 0 and len(executed) == 81
            assert await cold._repository.load_items(cold.thread_id) == items
            followup = [e async for e in cold.stream("Recall the completed work")]
            assert isinstance(followup[-1], TurnCompleted), followup[-1]
            assert cold_model.count == 1 and len(executed) == 81
            history = await cold._repository.load_items(cold.thread_id)
            assert history[: len(items)] == items
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("streamed", [False, True])
def test_long_turn_compacts_with_ordinary_request_and_continues(tmp_path, streamed):
    async def scenario():
        executed, summaries = [], []
        user_text = "Preserve this constraint and finish all work"

        class Tool:
            spec = ToolSpec("probe", "Count calls", {"type": "object"})

            async def execute(self, call, context):
                executed.append(call.id)
                return ToolResult(call.id, call.name, "ok")

        class Model:
            count = 0

            async def stream(self, request):
                turn, step = request.items[-1].turn_id, new_step_id()
                if not request.tools:
                    summaries.append(self.count)
                    yield ModelCompleted(
                        (
                            AssistantMessageItem(
                                "Completed prior probes; preserve constraint.", turn, step
                            ),
                        ),
                        ModelUsage(20, 10),
                    )
                    return
                self.count += 1
                assert (
                    sum(
                        isinstance(i, UserMessageItem) and i.content == user_text
                        for i in request.items
                    )
                    == 1
                )
                calls = {i.call.id for i in request.items if isinstance(i, ToolCallItem)}
                results = {i.call_id for i in request.items if isinstance(i, ToolResultItem)}
                assert calls == results
                items = (
                    (ToolCallItem(ToolCall(ToolCallId(str(self.count)), "probe", {}), turn, step),)
                    if self.count <= 27
                    else (AssistantMessageItem("done", turn, step),)
                )
                if streamed:
                    for item in items:
                        yield ModelItemCompleted(item)
                yield ModelCompleted(items, ModelUsage(60_000 if self.count == 26 else 100, 10))

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Tool())
        model = Model()
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path, skills_enabled=False, plugins_enabled=False, auto_compact_tokens=50_000
            ),
            registry=registry,
            model=model,
            database_path=tmp_path / "compact.db",
            home_path=tmp_path / "home",
        )
        try:
            events = [e async for e in runtime.stream(user_text)]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert summaries == [26] and model.count == 28
            assert sum(isinstance(e, ContextCompacted) for e in events) == 1
            assert executed == [ToolCallId(str(i)) for i in range(1, 28)]
            history = await runtime._repository.load_items(runtime.thread_id)
            assert sum(isinstance(i, ToolCallItem) for i in history) == 27
            assert sum(isinstance(i, ToolResultItem) for i in history) == 27
            users = [
                i for i in history if isinstance(i, UserMessageItem) and i.content == user_text
            ]
            originals = [i for i in users if i.retained_from_id is None]
            assert len(originals) == 1
            assert len(users) == 2 and users[-1].retained_from_id == originals[0].id
            assert (
                sum(
                    isinstance(i, UserMessageItem) and i.content == user_text
                    for i in active_history(history)
                )
                == 1
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
