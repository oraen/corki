import asyncio
from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.models import ModelCompleted, ModelItemCompleted
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import ContextCompacted, TurnCompleted
from corki.protocol.ids import new_tool_call_id, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall, ToolExposure, ToolResult, ToolSpec
from corki.sessions import TurnRecord, TurnStatus
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["native", "compatible"])
@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("change_kind", ["metadata", "input_schema", "output_schema"])
def test_replaced_search_handler_cannot_change_an_already_prepared_step(
    tmp_path, mode, streamed, change_kind
):
    async def scenario():
        requests, executed = [], []
        registry = ToolRegistry()
        owner = registry.create_owner()

        class Tool:
            def __init__(self, word, enum=True, output_enum=True):
                self.word = word
                self.enum = enum
                self.output_enum = output_enum
                self.spec = ToolSpec(
                    "lookup",
                    word,
                    {"type": "object", "properties": {"value": {"enum": [enum]}}},
                    exposure=ToolExposure.DEFERRED,
                    source=word,
                    output_schema={
                        "type": "object",
                        "properties": {"value": {"enum": [output_enum]}},
                    },
                )

            async def execute(self, call, context):
                executed.append((self.word, self.enum, self.output_enum))
                return ToolResult(call.id, call.name, self.word)

        old = Tool("amber")
        registry.replace_owned(owner, (old,))

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    previous = registry.get("tool_search")
                    registry.replace_owned(
                        owner,
                        (
                            Tool("cobalt")
                            if change_kind == "metadata"
                            else Tool("amber", enum=1)
                            if change_kind == "input_schema"
                            else Tool("amber", output_enum=1),
                        ),
                    )
                    await runtime._refresh_tools()
                    assert registry.get("tool_search") is not previous
                    call = ToolCall(new_tool_call_id(), "tool_search", {"query": "amber"})
                elif len(requests) == 2:
                    raw = await runtime._repository.load_items(runtime._thread_id)
                    found = next(
                        i
                        for i in raw
                        if isinstance(i, ToolResultItem) and i.tool_name == "tool_search"
                    )
                    assert found.discovered_tools == (old.spec,)
                    visible = next(
                        i
                        for i in request.items
                        if isinstance(i, ToolResultItem) and i.tool_name == "tool_search"
                    )
                    assert visible.discovered_tools == ()
                    assert "search again" in visible.content
                    assert "lookup" not in {spec.name for spec in request.tools}
                    call = ToolCall(
                        new_tool_call_id(),
                        "tool_search",
                        {"query": "cobalt" if change_kind == "metadata" else "amber"},
                    )
                elif len(requests) == 3:
                    loaded = next(spec for spec in request.tools if spec.name == "lookup")
                    assert loaded.description == (
                        "cobalt" if change_kind == "metadata" else "amber"
                    )
                    enum_value = loaded.parameters["properties"]["value"]["enum"][0]
                    assert type(enum_value) is (int if change_kind == "input_schema" else bool)
                    output_enum = loaded.output_schema["properties"]["value"]["enum"][0]
                    assert type(output_enum) is (int if change_kind == "output_schema" else bool)
                    call = ToolCall(new_tool_call_id(), "lookup", {})
                else:
                    assert len(requests) == 4 and executed == [
                        ("cobalt", True, True)
                        if change_kind == "metadata"
                        else ("amber", 1, True)
                        if change_kind == "input_schema"
                        else ("amber", True, 1)
                    ]
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))
                    return
                item = ToolCallItem(call, turn, step)
                if streamed:
                    yield ModelItemCompleted(item)
                yield ModelCompleted((item,))

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                tool_search_mode=mode,
                model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
                api_mode="responses",
            ),
            database_path=tmp_path / "search.db",
            registry=registry,
            model=Model(),
        )
        try:
            events = [e async for e in runtime.stream("refresh after sampling begins")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["removed", "replaced", "hidden", "code_mode_only"])
def test_cold_search_commit_recovery_projects_stale_definitions_before_budget(
    tmp_path, monkeypatch, change
):
    class Sink:
        async def emit(self, event):
            pass

    class Tool:
        spec = ToolSpec(
            "lookup", "amber " * 8000, {}, exposure=ToolExposure.DEFERRED, search_text="amber"
        )

        async def execute(self, call, context):
            raise AssertionError("search does not execute its hit")

    async def scenario():
        requests, executed = [], []

        class Replacement(Tool):
            spec = (
                replace(Tool.spec, exposure=ToolExposure(change))
                if change in {"hidden", "code_mode_only"}
                else replace(Tool.spec, description="cobalt", search_text="cobalt")
            )

            async def execute(self, call, context):
                executed.append(call.id)
                return ToolResult(call.id, call.name, "NEW_TOOL_RESULT")

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(new_tool_call_id(), "tool_search", {"query": "amber"}),
                                turn,
                                step,
                            ),
                        )
                    )
                elif len(requests) == 2:
                    result = next(i for i in request.items if isinstance(i, ToolResultItem))
                    assert result.discovered_tools == ()
                    assert "search again" in result.content
                    assert "lookup" not in {spec.name for spec in request.tools}
                    assert any(
                        isinstance(i, UserMessageItem) and i.content == "RECOVER THIS INPUT"
                        for i in request.items
                    )
                    if change == "replaced":
                        yield ModelCompleted(
                            (
                                ToolCallItem(
                                    ToolCall(
                                        new_tool_call_id(), "tool_search", {"query": "cobalt"}
                                    ),
                                    turn,
                                    step,
                                ),
                            )
                        )
                    elif change in {"hidden", "code_mode_only"}:
                        # A model can still guess an old loaded name. Dispatch
                        # must enforce the current exposure, not historical search.
                        yield ModelCompleted(
                            (ToolCallItem(ToolCall(new_tool_call_id(), "lookup", {}), turn, step),)
                        )
                    else:
                        yield ModelCompleted((AssistantMessageItem("done", turn, step),))
                elif len(requests) == 3:
                    assert not executed
                    if change in {"hidden", "code_mode_only"}:
                        result = next(
                            i
                            for i in request.items
                            if isinstance(i, ToolResultItem) and i.tool_name == "lookup"
                        )
                        assert result.is_error
                        assert "tool was not advertised for this step: lookup" in result.content
                        assert "lookup" not in {spec.name for spec in request.tools}
                        yield ModelCompleted((AssistantMessageItem("done", turn, step),))
                        return
                    assert (
                        next(s for s in request.tools if s.name == "lookup").description == "cobalt"
                    )
                    yield ModelCompleted(
                        (ToolCallItem(ToolCall(new_tool_call_id(), "lookup", {}), turn, step),)
                    )
                else:
                    assert len(requests) == 4 and len(executed) == 1
                    result = next(
                        i
                        for i in request.items
                        if isinstance(i, ToolResultItem) and i.tool_name == "lookup"
                    )
                    assert not result.is_error and result.content == "NEW_TOOL_RESULT"
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            plugins_enabled=False,
            context_window_tokens=18000,
            auto_compact_tokens=8000,
        )
        registry = ToolRegistry()
        registry.register(Tool())
        database = tmp_path / "cold-budget.db"
        warm = await LangGraphRuntime.acreate(
            settings=settings, database_path=database, registry=registry, model=Model()
        )
        try:
            await warm._ensure_ready()
            thread, turn = warm.thread_id, new_turn_id()
            user = UserMessageItem("RECOVER THIS INPUT", turn)
            repository = warm._repository
            await repository.save_turn(TurnRecord(turn, thread, TurnStatus.RUNNING, user.content))
            await repository.append_items(thread, (user,))
            reached = asyncio.Event()
            append = type(repository).append_items

            async def held_commit(self, thread_id, items):
                await append(self, thread_id, items)
                if any(isinstance(i, ToolResultItem) for i in items):
                    reached.set()
                    await asyncio.Event().wait()

            with monkeypatch.context() as patch:
                patch.setattr(type(repository), "append_items", held_commit)
                task = asyncio.create_task(
                    warm._compiled.ainvoke(
                        _initial_state(thread, turn, settings, user),
                        context=GraphRunContext(events=Sink()),
                        config=warm._graph_config(turn),
                    )
                )
                try:
                    await asyncio.wait_for(reached.wait(), 5)
                finally:
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
            original = await repository.load_items(thread)
            assert len(requests) == 1
            assert next(i for i in original if isinstance(i, ToolResultItem)).discovered_tools == (
                Tool.spec,
            )
            assert await warm._checkpointer.aget_tuple(warm._graph_config(turn)) is not None
        finally:
            await warm.aclose()

        cold_registry = ToolRegistry()
        if change != "removed":
            cold_registry.register(Replacement())
        cold = await LangGraphRuntime.acreate(
            settings=settings,
            database_path=database,
            registry=cold_registry,
            model=Model(),
            thread_id=thread,
        )
        try:
            events = [e async for e in cold.resume_pending()]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            expected_results = {"removed": 1, "replaced": 3, "hidden": 2, "code_mode_only": 2}
            assert len(requests) == expected_results[change] + 1
            assert not any(isinstance(e, ContextCompacted) for e in events)
            stored = await cold._repository.load_items(thread)
            assert stored[: len(original)] == original
            results = [i for i in stored if isinstance(i, ToolResultItem)]
            assert len(results) == expected_results[change]
            assert len([i for i in stored if isinstance(i, UserMessageItem)]) == 1
            assert await cold._repository.latest_running_turn(thread) is None
            assert [e async for e in cold.resume_pending()] == []
            assert len(executed) == int(change == "replaced")
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["removed", "replaced"])
def test_unavailable_large_search_result_does_not_consume_request_budget(tmp_path, change):
    async def scenario():
        registry = ToolRegistry()
        owner = registry.create_owner()
        requests = []

        class Tool:
            spec = ToolSpec(
                "lookup",
                "amber " * 8000,
                {"type": "object", "properties": {}},
                exposure=ToolExposure.DEFERRED,
                search_text="amber",
            )

            async def execute(self, call, context):
                raise AssertionError("discovery must not execute the tool")

        old = Tool()
        registry.replace_owned(owner, (old,))

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    new = Tool()
                    new.spec = replace(old.spec, description="cobalt", search_text="cobalt")
                    registry.replace_owned(owner, () if change == "removed" else (new,))
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(new_tool_call_id(), "tool_search", {"query": "amber"}),
                                turn,
                                step,
                            ),
                        )
                    )
                else:
                    result = next(i for i in request.items if isinstance(i, ToolResultItem))
                    assert result.discovered_tools == (), "budget used an obsolete definition"
                    assert "search again" in result.content
                    assert "amber amber" not in result.content
                    assert "lookup" not in {spec.name for spec in request.tools}
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                model="fixture",
                context_window_tokens=18000,
                auto_compact_tokens=8000,
            ),
            database_path=tmp_path / "budget.db",
            registry=registry,
            model=Model(),
        )
        try:
            events = [e async for e in runtime.stream("CURRENT INPUT VERBATIM")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 2
            assert not any(isinstance(e, ContextCompacted) for e in events)
            stored = await runtime._repository.load_items(runtime.thread_id)
            result = next(i for i in stored if isinstance(i, ToolResultItem))
            assert result.discovered_tools == (old.spec,)
            assert "amber amber" in result.content
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
