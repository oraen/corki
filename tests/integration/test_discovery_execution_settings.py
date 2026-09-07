import asyncio
from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall, ToolConcurrency, ToolExposure, ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["native", "compatible"])
@pytest.mark.parametrize("cold", [False, True])
def test_loaded_tools_survive_execution_settings_updates_across_turns_and_reopen(
    tmp_path, mode, cold
):
    async def scenario():
        requests, entered = [], []
        both_started = asyncio.Event()
        original = tuple(
            ToolSpec(name, "records", {}, exposure=ToolExposure.DEFERRED, output_char_budget=1000)
            for name in ("first", "second")
        )
        changed = tuple(
            replace(spec, concurrency=ToolConcurrency.PARALLEL, output_char_budget=80)
            for spec in original
        )

        class Tool:
            def __init__(self, spec):
                self.spec = spec

            async def execute(self, call, context):
                entered.append(call.name)
                if len(entered) == 2:
                    both_started.set()
                # Sequential use of the old execution policy fails instead of hanging.
                await asyncio.wait_for(both_started.wait(), timeout=2)
                return ToolResult(call.id, call.name, "x" * 400)

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                count = len(requests)
                if count == 1:
                    calls = (ToolCall(new_tool_call_id(), "tool_search", {"query": "records"}),)
                elif count == 2:
                    yield ModelCompleted((AssistantMessageItem("loaded", turn, step),))
                    return
                elif count == 3:
                    found = next(
                        i
                        for i in request.items
                        if isinstance(i, ToolResultItem) and i.tool_name == "tool_search"
                    )
                    assert found.discovered_tools == original
                    visible = tuple(
                        spec for spec in request.tools if spec.name in {"first", "second"}
                    )
                    assert visible == (
                        tuple(replace(spec, exposure=ToolExposure.DIRECT) for spec in changed)
                        if mode == "compatible"
                        else ()
                    )
                    calls = tuple(ToolCall(new_tool_call_id(), spec.name, {}) for spec in changed)
                else:
                    assert count == 4 and set(entered) == {"first", "second"}
                    results = [
                        i
                        for i in request.items
                        if isinstance(i, ToolResultItem) and i.tool_name in {"first", "second"}
                    ]
                    assert len(results) == 2 and all(
                        not i.is_error and len(i.content) < 400 for i in results
                    )
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))
                    return
                yield ModelCompleted(tuple(ToolCallItem(call, turn, step) for call in calls))

            async def aclose(self):
                pass

        registry = ToolRegistry()
        owner = registry.create_owner()
        registry.replace_owned(owner, tuple(Tool(spec) for spec in original))
        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            tool_search_mode=mode,
            api_mode="responses",
        )
        database = tmp_path / "loaded.db"
        runtime = LangGraphRuntime.create(
            settings=settings, database_path=database, registry=registry, model=Model()
        )
        try:
            events = [e async for e in runtime.stream("discover records")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            if cold:
                thread = runtime.thread_id
                await runtime.aclose()
                registry = ToolRegistry()
                for spec in changed:
                    registry.register(Tool(spec))
                runtime = LangGraphRuntime.create(
                    settings=settings,
                    database_path=database,
                    registry=registry,
                    model=Model(),
                    thread_id=thread,
                )
            else:
                registry.replace_owned(owner, tuple(Tool(spec) for spec in changed))
            events = [e async for e in runtime.stream("call both tools")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 4
            raw = await runtime._repository.load_items(runtime.thread_id)
            searches = [
                i for i in raw if isinstance(i, ToolResultItem) and i.tool_name == "tool_search"
            ]
            assert len(searches) == 1 and searches[0].discovered_tools == original
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
