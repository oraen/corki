"""A copied search observation loads only currently valid ordinary tool definitions."""

import asyncio
from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall, ToolExposure, ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize("catalog", ["unchanged", "changed", "removed"])
def test_fork_search_revalidates_catalog_without_replaying_source(tmp_path, catalog):
    async def scenario():
        effects = []
        base = ToolSpec(
            "calendar", "calendar meeting", {"type": "object"}, exposure=ToolExposure.DEFERRED
        )

        class Tool:
            def __init__(self, spec):
                self.spec = spec

            async def execute(self, call, context):
                effects.append(call.id)
                return ToolResult(call.id, call.name, "CREATED")

        class Model:
            def __init__(self, phase, spec):
                self.phase, self.spec, self.requests = phase, spec, []

            async def stream(self, request):
                self.requests.append(request)
                names = {s.name for s in request.tools}
                results = [i for i in request.items if isinstance(i, ToolResultItem)]
                first = len(self.requests) == 1
                if first and self.phase != "source":
                    assert any(
                        i.tool_name == "calendar" and i.content == "CREATED" for i in results
                    )
                    assert ("calendar" in names) == (catalog == "unchanged" or self.phase == "cold")
                    if catalog != "unchanged" and self.phase == "fork":
                        search = next(i for i in results if i.tool_name == "tool_search")
                        assert search.discovered_tools == ()
                if catalog == "removed" and self.phase != "source":
                    yield ModelCompleted(())
                    return
                if "calendar" not in names:
                    call = ToolCall(new_tool_call_id(), "tool_search", {"query": "calendar"})
                elif first or self.requests[-2].tools != request.tools:
                    loaded = next(s for s in request.tools if s.name == "calendar")
                    assert loaded.description == self.spec.description
                    call = ToolCall(new_tool_call_id(), "calendar", {})
                else:
                    assert results[-1].content == "CREATED"
                    yield ModelCompleted(())
                    return
                yield ModelCompleted(
                    (ToolCallItem(call, request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        async def create(phase, **kwargs):
            spec = (
                replace(base, description="changed calendar meeting")
                if (phase != "source" and catalog == "changed")
                else base
            )
            registry = ToolRegistry()
            if phase == "source" or catalog != "removed":
                registry.register(Tool(spec))
            model = Model(phase, spec)
            runtime = await LangGraphRuntime.acreate(
                settings=CorkiSettings(tmp_path, skills_enabled=False, plugins_enabled=False),
                model=model,
                registry=registry,
                database_path=tmp_path / "state.db",
                home_path=tmp_path,
                **kwargs,
            )
            return runtime, model

        source, source_model = await create("source")
        try:
            assert isinstance([e async for e in source.stream("SEED")][-1], TurnCompleted)
            assert len(source_model.requests) == 3 and len(effects) == 1
            original = await source.load_display_snapshot()
            source_id = source.thread_id
        finally:
            await source.aclose()
        fork, model = await create("fork", fork_from_thread_id=source_id)
        try:
            assert [e async for e in fork.resume_pending()] == []
            assert len(effects) == 1 and not model.requests
            assert isinstance([e async for e in fork.stream("BRANCH")][-1], TurnCompleted)
            assert len(effects) == (1 if catalog == "removed" else 2)
            assert len(model.requests) == {"unchanged": 2, "changed": 3, "removed": 1}[catalog]
            assert await fork._repository.load_display_snapshot(source_id) == original
            fork_id = fork.thread_id
        finally:
            await fork.aclose()
        if catalog != "removed":
            cold, model = await create("cold", thread_id=fork_id)
            try:
                assert [e async for e in cold.resume_pending()] == []
                assert isinstance([e async for e in cold.stream("COLD")][-1], TurnCompleted)
                assert len(model.requests) == 2 and len(effects) == 3
                assert len(set(effects)) == 3
                assert await cold._repository.load_display_snapshot(source_id) == original
            finally:
                await cold.aclose()

    asyncio.run(scenario())
