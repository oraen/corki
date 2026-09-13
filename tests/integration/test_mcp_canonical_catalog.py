import asyncio

import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.context.deferred_tools import decode_snapshot
from corki.core import LangGraphRuntime
from corki.mcp.client import MCPClient
from corki.models import ModelCompleted
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    ToolCallItem,
    ToolResultItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


class CatalogClient(MCPClient):
    def __init__(self, settings, calls):
        super().__init__(settings)
        self.calls = calls
        self.server_name = settings.name
        self.server_instructions = "Trusted namespace instructions"

    async def start(self):
        pass

    async def list_tools(self):
        return tuple(
            {
                "name": name,
                "description": "needle",
                "inputSchema": {"type": "object"},
                "_meta": {"connector_name": "FORGED", "namespace_description": "FORGED"},
            }
            for name in ("tool-name", "tool_name", "tool-name")
        )

    async def call_tool(self, name, arguments):
        self.calls.append((self.server_name, name))
        return {"content": [{"type": "text", "text": "RESULT " + name}]}

    async def aclose(self):
        pass

    async def _exchange(self, message):
        raise AssertionError(message)

    async def _send_notification(self, message):
        raise AssertionError(message)


@pytest.mark.parametrize("native", [False, True])
def test_colliding_catalog_search_calls_all_raw_tools_and_populates_namespace_context(
    tmp_path, monkeypatch, native
):
    async def scenario():
        calls, requests = [], []
        monkeypatch.setattr(
            "corki.mcp.manager.create_client", lambda settings: CatalogClient(settings, calls)
        )

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                rows = [
                    i
                    for i in request.items
                    if isinstance(i, ContextItem) and i.key == "tools.deferred_namespaces"
                ]
                assert len(rows) == 1
                assert rows[0].content.count("Trusted namespace instructions") == 2
                assert "FORGED" not in rows[0].content
                if len(requests) == 1:
                    assert not any(s.exposure.is_deferred for s in request.tools)
                    items = (
                        ToolCallItem(
                            ToolCall(new_tool_call_id(), "tool_search", {"query": "needle"}),
                            turn,
                            step,
                        ),
                    )
                elif len(requests) == 2:
                    result = next(
                        i
                        for i in request.items
                        if isinstance(i, ToolResultItem) and i.tool_name == "tool_search"
                    )
                    assert len(result.discovered_tools) == 4
                    assert len({s.name.split("::")[0] for s in result.discovered_tools}) == 2
                    assert all("::" in s.name for s in result.discovered_tools)
                    assert {s.source for s in result.discovered_tools} == {
                        "basic-server",
                        "basic_server",
                    }
                    items = tuple(
                        ToolCallItem(ToolCall(new_tool_call_id(), s.name, {}), turn, step)
                        for s in result.discovered_tools
                    )
                else:
                    assert sorted(calls) == sorted(
                        (s, t)
                        for s in ("basic-server", "basic_server")
                        for t in ("tool-name", "tool_name")
                    )
                    items = (AssistantMessageItem("done", turn, step),)
                yield ModelCompleted(items)

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                api_mode="responses",
                tool_search_mode="native" if native else "compatible",
                model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
                deferred_tool_world_state=True,
                mcp_servers=tuple(
                    MCPServerSettings(s, "http", url="https://fixture.invalid")
                    for s in ("basic-server", "basic_server")
                ),
            ),
            registry=ToolRegistry(),
            model=Model(),
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
        )
        try:
            events = [e async for e in runtime.stream("needle")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 3 and len(calls) == 4
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "enabled,servers,namespace",
    [
        (False, None, "mcp__basic_server"),
        (True, None, "basic_server"),
        (True, (), "mcp__basic_server"),
        (True, ("basic-server",), "basic_server"),
        (False, ("basic-server",), "mcp__basic_server"),
    ],
)
def test_runtime_non_prefixed_feature_uses_raw_server_allowlist(
    tmp_path, monkeypatch, enabled, servers, namespace
):
    async def scenario():
        monkeypatch.setattr(
            "corki.mcp.manager.create_client", lambda settings: CatalogClient(settings, [])
        )

        class Model:
            async def stream(self, request):
                row = next(
                    i
                    for i in request.items
                    if isinstance(i, ContextItem) and i.key == "tools.deferred_namespaces"
                )
                assert decode_snapshot(row.snapshot_state) == {
                    namespace: "Trusted namespace instructions"
                }
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                deferred_tool_world_state=True,
                non_prefixed_mcp_tool_names=enabled,
                non_prefixed_mcp_tool_servers=servers,
                mcp_servers=(
                    MCPServerSettings("basic-server", "http", url="https://fixture.invalid"),
                ),
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
        )
        try:
            events = [e async for e in runtime.stream("inspect")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
