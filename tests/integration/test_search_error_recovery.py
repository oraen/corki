"""Search argument errors remain observations, not implicit tool discovery."""

import asyncio
from copy import copy

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall, ToolExposure, ToolResult, ToolSpec
from corki.tools import ToolRegistry
from corki.tools.search import ToolSearchIndex, ToolSearchTool


@pytest.mark.parametrize(
    "arguments",
    [
        {"query": "  "},
        {"query": 123},
        {"query": "vaultproof", "limit": 0},
        "index_fault",
        "invalid_definition",
        "invalid_definition_mapping",
        "invalid_definition_object",
        "invalid_definition_nan",
        "invalid_definition_surrogate",
    ],
)
def test_failed_search_does_not_load_tool_and_corrected_search_recovers(
    tmp_path, monkeypatch, arguments
):
    if arguments == "index_fault":
        rank = ToolSearchIndex.rank
        failed = False

        def fail_once(self, *args, **kwargs):
            nonlocal failed
            if not failed:
                failed = True
                raise RuntimeError("fixture search index failure")
            return rank(self, *args, **kwargs)

        monkeypatch.setattr(ToolSearchIndex, "rank", fail_once)
        arguments = {"query": "vaultproof"}
    elif isinstance(arguments, str) and arguments.startswith("invalid_definition"):
        execute = ToolSearchTool.execute
        invalid = None if arguments == "invalid_definition" else {"name": "vault::read"}
        malformed_parameters = {
            "invalid_definition_object": {"default": object()},
            "invalid_definition_nan": {"default": float("nan")},
            "invalid_definition_surrogate": {"description": "\ud800"},
        }
        if arguments in malformed_parameters:
            # A normal ToolSpec now rejects these at construction. Simulate a
            # misbehaving external search handler bypassing that admission gate
            # to keep the result-publication boundary independently covered.
            invalid = copy(ToolSpec("vault::read", "vaultproof", {}))
            object.__setattr__(invalid, "parameters", malformed_parameters[arguments])
        failed = False

        async def malformed_once(self, call, context):
            nonlocal failed
            if not failed:
                failed = True
                return ToolResult(
                    call.id, call.name, "bad definitions", discovered_tools=(invalid,)
                )
            return await execute(self, call, context)

        monkeypatch.setattr(ToolSearchTool, "execute", malformed_once)
        arguments = {"query": "vaultproof"}

    async def scenario():
        executions = []
        requests = []

        class Read:
            spec = ToolSpec("vault::read", "vaultproof", {}, exposure=ToolExposure.DEFERRED)

            async def execute(self, call, context):
                executions.append(call.id)
                return ToolResult(call.id, call.name, "VAULT_OBSERVATION")

        class Model:
            async def stream(self, request):
                requests.append(request)
                index = len(requests)
                names = {tool.name for tool in request.tools}
                results = [item for item in request.items if isinstance(item, ToolResultItem)]
                if index <= 3:
                    assert "vault::read" not in names
                    assert not executions
                if index == 1:
                    name, args = "tool_search", arguments
                elif index == 2:
                    assert results[-1].tool_name == "tool_search" and results[-1].is_error
                    assert not results[-1].discovered_tools
                    name, args = "vault::read", {}
                elif index == 3:
                    assert results[-1].tool_name == "vault::read" and results[-1].is_error
                    name, args = "tool_search", {"query": "vaultproof"}
                elif index == 4:
                    assert "vault::read" in names
                    assert not results[-1].is_error
                    assert [s.name for s in results[-1].discovered_tools] == ["vault::read"]
                    name, args = "vault::read", {}
                else:
                    assert len(executions) == 1
                    assert results[-1].content == "VAULT_OBSERVATION"
                    yield ModelCompleted(
                        (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                    )
                    return
                yield ModelCompleted(
                    (
                        ToolCallItem(
                            ToolCall(new_tool_call_id(), name, args),
                            request.items[-1].turn_id,
                            new_step_id(),
                        ),
                    )
                )

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Read())
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(tmp_path, skills_enabled=False),
            model=Model(),
            registry=registry,
            database_path=tmp_path / "search.db",
        )
        try:
            events = [event async for event in runtime.stream("read the vault")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 5
            raw = await runtime._repository.load_items(runtime.thread_id)
            results = [item for item in raw if isinstance(item, ToolResultItem)]
            assert [item.is_error for item in results] == [True, True, False, False]
            assert len({item.call_id for item in results}) == 4
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
