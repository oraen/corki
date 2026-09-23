"""Discover parameter/result contracts in real Code Mode, then call across turns."""

import asyncio
import json

import pytest
from test_mcp_pending_reuse import PendingClient

from corki.code_mode.service import CodeModeService
from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.manager import MCPManager
from corki.mcp.tool_catalog_cache import MCPToolCatalogCache
from corki.mcp.tools import MCPTool
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall, ToolExposure
from corki.tools import ToolRegistry


@pytest.mark.skipif(not CodeModeService.available(), reason="install corki[code-mode]")
@pytest.mark.parametrize("mode", ["code_mode", "code_mode_only"])
@pytest.mark.parametrize("exposure", [ToolExposure.DIRECT, ToolExposure.DEFERRED])
def test_discover_schema_call_observation_across_turns(tmp_path, mode, exposure):
    async def scenario():
        calls, requests = [], []

        class Client:
            async def call_tool(self, name, arguments):
                calls.append((name, arguments))
                # Deliberately outside the advertised enum: schema documents the
                # return value; the host must not turn it into a validation error.
                return {
                    "content": [],
                    "structuredContent": {
                        "status": "unexpected",
                        "record_id": arguments["record_id"],
                    },
                    "_meta": {"secret": "PRIVATE"},
                }

        handler = MCPTool(
            "docs",
            {
                "name": "read",
                "description": "Look up a document",
                "inputSchema": {
                    "type": "object",
                    "properties": {"record_id": {"type": "string"}},
                    "required": ["record_id"],
                },
                "outputSchema": {
                    "type": "object",
                    "properties": {"status": {"enum": ["advertised"]}},
                },
            },
            Client(),
            exposure=exposure,
        )
        registry = ToolRegistry()
        registry.register(handler)

        class Model:
            async def stream(self, request):
                requests.append(request)
                phase = (len(requests) - 1) % 3
                turn, step = request.items[-1].turn_id, new_step_id()
                results = [item for item in request.items if isinstance(item, ToolResultItem)]
                if phase == 0:
                    assert (handler.spec.name in {t.name for t in request.tools}) == (
                        mode == "code_mode" and exposure == ToolExposure.DIRECT
                    )
                    script = "text(ALL_TOOLS.find(t => t.name === 'mcp__docs__read'));"
                elif phase == 1:
                    metadata = json.loads(results[-1].content_items[1].text)
                    assert set(metadata) == {"name", "description"}
                    assert "record_id" in metadata["description"]
                    assert "structuredContent" in metadata["description"]
                    assert "advertised" in metadata["description"]
                    script = f"text(await tools.{metadata['name']}({{record_id:'abc'}}));"
                else:
                    result = results[-1]
                    assert not result.is_error, result.content
                    value = json.loads(result.content_items[1].text)
                    assert value == {
                        "content": [],
                        "structuredContent": {"status": "unexpected", "record_id": "abc"},
                    }
                    assert "PRIVATE" not in repr(request.items)
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))
                    return
                call = ToolCall(
                    new_tool_call_id(), "exec", None, raw_arguments=script, input_kind="freeform"
                )
                yield ModelCompleted((ToolCallItem(call, turn, step),))

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, tool_mode=mode
            ),
            registry=registry,
            model=Model(),
            database_path=tmp_path / "schema.db",
        )
        try:
            for _ in range(2):
                events = [event async for event in runtime.stream("find and read a document")]
                assert isinstance(events[-1], TurnCompleted), events[-1]
                assert not runtime._code_mode.cells
            assert len(requests) == 6 and calls == [("read", {"record_id": "abc"})] * 2
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_invalid_output_schema_cannot_seed_a_shared_catalog(monkeypatch):
    async def scenario():
        settings = MCPServerSettings("docs", "http", url="https://schema.test")
        cache, clients = MCPToolCatalogCache(), []

        class Client(PendingClient):
            async def list_tools(self):
                return [{"name": "read", "outputSchema": False}]

        def factory(settings):
            client = Client(settings, "unused")
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        registry = ToolRegistry()
        manager = MCPManager((settings,), registry, tool_catalog_cache=cache)
        try:
            await manager.start()
            assert not any(t.name.startswith("mcp__docs") for t in registry.specs())
            assert cache.context(settings).current_tools_or() is None
        finally:
            await manager.aclose()
        assert clients and all(client.closes == 1 for client in clients)

    asyncio.run(scenario())


def test_cached_output_metadata_survives_projection_without_mutating_raw_catalog(monkeypatch):
    async def scenario():
        settings = MCPServerSettings("docs", "http", url="https://schema.test")
        cache, clients = MCPToolCatalogCache(), []
        definition = {
            "name": "read",
            "outputSchema": {"enum": ["original"]},
            "annotations": {"readOnlyHint": True},
        }

        class Client(PendingClient):
            async def list_tools(self):
                self.lists += 1
                await self.pause("list")
                return [definition]

        def factory(settings):
            client = Client(settings, "list")
            if not clients:
                client.release.set()
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        registries = [ToolRegistry(), ToolRegistry()]
        managers = [
            MCPManager((settings,), registry, tool_catalog_cache=cache) for registry in registries
        ]
        try:
            await managers[0].start()
            frozen = registries[0].snapshot()
            definition["outputSchema"]["enum"] = ["mutated"]
            await managers[1].capture_tools(optional_startup_grace_ms=1)
            for registry in (frozen, *registries):
                spec = next(t for t in registry.specs() if t.name == "mcp__docs::read")
                assert spec.output_schema["properties"]["structuredContent"] == {
                    "enum": ["original"]
                }
                spec.output_schema["properties"].clear()
            snapshot = cache.context(settings).current_tools_or()
            assert snapshot.definitions == (
                {"name": "read", "outputSchema": {"enum": ["original"]}},
            )
            assert not clients[1].release.is_set()
            assert clients[0].starts == clients[0].lists == 1
        finally:
            for manager in managers:
                await manager.aclose()
        assert len(clients) == 2 and all(client.closes == 1 for client in clients)

    asyncio.run(scenario())
