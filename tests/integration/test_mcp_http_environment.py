import asyncio
import json

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import HttpMCPClient
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["native", "compatible"])
@pytest.mark.parametrize("plugin", [False, True])
def test_runtime_header_snapshots_reconciliation_plugin_failure_isolation_and_cold_history(
    tmp_path, monkeypatch, mode, plugin
):
    async def scenario():
        clients, good_clients, calls, deleted = [], [], [], []
        monkeypatch.setenv("CORKI_HTTP_TOKEN", "first")
        monkeypatch.setenv("CORKI_HTTP_HEADER", "first")
        monkeypatch.delenv("CORKI_HTTP_MISSING", raising=False)
        expected = "first"

        def factory(settings):
            snapshot = expected
            session = str(len(good_clients))

            def handler(request):
                assert settings.name != "bad", "missing bearer reached transport"
                assert request.headers["authorization"] == f"Bearer {snapshot}"
                assert request.headers["x-key"] == snapshot
                assert request.headers["user-agent"] == "fixture-agent"
                if request.method == "DELETE":
                    deleted.append(session)
                    return httpx.Response(204)
                message = json.loads(request.content)
                if message["method"] == "notifications/initialized":
                    return httpx.Response(202)
                if message["method"] == "initialize":
                    result = {
                        "serverInfo": {"name": "fixture", "version": "1"},
                        "protocolVersion": "2025-06-18",
                        "capabilities": {"tools": {}},
                    }
                elif message["method"] == "tools/list":
                    result = {
                        "tools": [
                            {
                                "name": "lookup",
                                "description": "header needle",
                                "inputSchema": {"type": "object"},
                            }
                        ]
                    }
                else:
                    assert message["method"] == "tools/call"
                    assert request.headers["mcp-session-id"] == session
                    calls.append((session, snapshot))
                    result = {"content": [{"type": "text", "text": snapshot}]}
                return httpx.Response(
                    200,
                    headers={"mcp-session-id": session},
                    json={
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "result": result,
                    },
                )

            client = HttpMCPClient(settings, transport=httpx.MockTransport(handler))
            clients.append(client)
            if settings.name != "bad":
                good_clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        config = {
            "url": "https://fixture.test",
            "bearer_token_env_var": "CORKI_HTTP_TOKEN",
            "http_headers": {"X-Key": "literal", "User-Agent": "fixture-agent"},
            "env_http_headers": {"x-key": "CORKI_HTTP_HEADER"},
        }
        bad = MCPServerSettings.from_mapping(
            "bad", {"url": "https://fixture.test/bad", "bearer_token_env_var": "CORKI_HTTP_MISSING"}
        )
        if plugin:
            manifest = tmp_path / ".corki/plugins/fixture/.codex-plugin/plugin.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({"name": "fixture", "mcpServers": {"docs": config}}))
        settings = CorkiSettings(
            working_directory=tmp_path,
            plugin_dirs=(tmp_path / ".corki/plugins",),
            skills_enabled=False,
            api_mode="responses",
            tool_search_mode=mode,
            mcp_servers=(bad,) if plugin else (bad, MCPServerSettings.from_mapping("docs", config)),
        )
        name = "mcp__docs::lookup"

        class Model:
            count = 0

            async def stream(self, request):
                nonlocal expected
                self.count += 1
                turn, step = request.items[-1].turn_id, new_step_id()
                assert len(runtime._mcp_manager.warnings) == 1
                assert "CORKI_HTTP_MISSING" in runtime._mcp_manager.warnings[0]
                if self.count == 1:
                    call = ToolCall(new_tool_call_id(), "tool_search", {"query": "needle"})
                elif self.count == 5:
                    assert calls == [("0", "first"), ("1", "second"), ("1", "second")]
                    assert len(good_clients) == 2
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))
                    return
                else:
                    if self.count == 3:
                        assert calls == [("0", "first")]
                        expected = "second"
                        monkeypatch.setenv("CORKI_HTTP_TOKEN", expected)
                        monkeypatch.setenv("CORKI_HTTP_HEADER", expected)
                        runtime.request_mcp_reconcile()
                    elif self.count == 4:
                        monkeypatch.setenv("CORKI_HTTP_UNRELATED", "changed")
                        runtime.request_mcp_reconcile()
                    call = ToolCall(new_tool_call_id(), name, {})
                yield ModelCompleted((ToolCallItem(call, turn, step),))

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
        )
        try:
            events = [e async for e in runtime.stream("header needle")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            raw = await runtime._repository.load_items(runtime._thread_id)
        finally:
            await runtime.aclose()
        assert deleted == ["0", "1"] and all(c.is_closed for c in clients)

        class ColdModel:
            async def stream(self, request):
                assert (
                    len(
                        [
                            i
                            for i in request.items
                            if isinstance(i, ToolResultItem) and i.tool_name == name
                        ]
                    )
                    == 3
                )
                yield ModelCompleted(
                    (AssistantMessageItem("cold", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        cold = await LangGraphRuntime.acreate(
            settings=settings,
            model=ColdModel(),
            registry=ToolRegistry(),
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
            thread_id=runtime._thread_id,
        )
        try:
            events = [e async for e in cold.stream("continue")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            after = await cold._repository.load_items(cold._thread_id)
            assert after[: len(raw)] == raw
            assert good_clients[-1]._next_id == 2
        finally:
            await cold.aclose()
        assert deleted == ["0", "1", "2"] and all(c.is_closed for c in clients)
        assert calls == [("0", "first"), ("1", "second"), ("1", "second")]

    asyncio.run(scenario())
