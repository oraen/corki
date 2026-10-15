import asyncio
import json
import os
import shlex
import sys

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

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX helper process contract")


@pytest.mark.parametrize("mode", ["native", "compatible"])
@pytest.mark.parametrize("plugin", [False, True])
def test_runtime_helper_auth_refresh_reconciliation_plugin_and_cold_history(
    tmp_path, monkeypatch, mode, plugin
):
    async def scenario():
        count = tmp_path / "count"
        script = (
            "import json; from pathlib import Path; "
            f"p=Path({str(count)!r}); n=int(p.read_text())+1 if p.exists() else 1; "
            "p.write_text(str(n)); print(json.dumps({'X-Gateway':str(n),'X-Label':'café'}))"
        )
        command = shlex.join([sys.executable, "-c", script])
        clients, calls, executed, deleted = [], [], [], []

        def factory(settings):
            session = str(len(clients))

            def handler(request):
                gateway = request.headers["x-gateway"]
                assert request.headers["x-label"] == "café"
                if request.method == "DELETE":
                    deleted.append((session, gateway))
                    return httpx.Response(204)
                message = json.loads(request.content)
                if "id" not in message:
                    return httpx.Response(202)
                method = message["method"]
                if method == "initialize":
                    result = {
                        "serverInfo": {"name": "fixture", "version": "1"},
                        "protocolVersion": "2025-06-18",
                        "capabilities": {"tools": {}},
                    }
                elif method == "tools/list":
                    result = {
                        "tools": [
                            {
                                "name": "read",
                                "description": "helper needle",
                                "inputSchema": {"type": "object"},
                            }
                        ]
                    }
                else:
                    assert method == "tools/call"
                    calls.append((session, gateway, request.content))
                    if gateway == "1":
                        return httpx.Response(
                            401,
                            headers={"www-authenticate": 'Bearer realm="fixture"'},
                            content=b"challenge",
                        )
                    executed.append((session, gateway))
                    result = {"content": [{"type": "text", "text": gateway}]}
                return httpx.Response(
                    200,
                    headers={"mcp-session-id": session},
                    json={"jsonrpc": "2.0", "id": message["id"], "result": result},
                )

            client = HttpMCPClient(settings, transport=httpx.MockTransport(handler))
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        config = {"url": "https://fixture.test", "http_headers_helper": command}
        if plugin:
            manifest = tmp_path / ".corki/plugins/fixture/.codex-plugin/plugin.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({"name": "fixture", "mcpServers": {"docs": config}}))
        name = "mcp__docs::read"
        settings = CorkiSettings(
            working_directory=tmp_path,
            plugin_dirs=(tmp_path / ".corki/plugins",),
            skills_enabled=False,
            api_mode="responses",
            tool_search_mode=mode,
            mcp_servers=() if plugin else (MCPServerSettings.from_mapping("docs", config),),
        )

        class Model:
            count = 0

            async def stream(self, request):
                self.count += 1
                assert not runtime._mcp_manager.warnings
                turn, step = request.items[-1].turn_id, new_step_id()
                if self.count == 1:
                    call = ToolCall(new_tool_call_id(), "tool_search", {"query": "needle"})
                elif self.count == 5:
                    assert executed == [("0", "2"), ("0", "2"), ("1", "3")]
                    assert (
                        calls[0][2] == calls[1][2]
                    )  # Auth retry preserves exact call identity/body.
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))
                    return
                else:
                    if self.count == 3:
                        runtime.request_mcp_reconcile()
                    elif self.count == 4:
                        assert count.read_text() == "2" and len(clients) == 1
                        runtime.request_mcp_refresh()
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
            events = [e async for e in runtime.stream("helper needle")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            raw = await runtime._repository.load_items(runtime._thread_id)
        finally:
            await runtime.aclose()
        assert deleted == [("0", "2"), ("1", "3")]

        class Cold:
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
            model=Cold(),
            registry=ToolRegistry(),
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
            thread_id=runtime._thread_id,
        )
        try:
            events = [e async for e in cold.stream("continue")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            after = await cold._repository.load_items(cold._thread_id)
            assert after[: len(raw)] == raw and len(executed) == 3
            assert clients[-1]._next_id == 2
        finally:
            await cold.aclose()
        assert count.read_text() == "4" and all(c.is_closed for c in clients)

    asyncio.run(scenario())
