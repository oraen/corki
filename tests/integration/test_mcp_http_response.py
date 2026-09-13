import asyncio
import json

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.runtime_environment import MCPHTTPEnvironment, MCPRuntimeContext
from corki.models import ModelCompleted
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["native", "compatible"])
@pytest.mark.parametrize("status", [400, 500])
@pytest.mark.parametrize("bound", [False, True])
def test_loopback_search_remote_http_error_observation_and_cold_history(
    tmp_path, mode, status, bound
):
    async def scenario():
        tasks, calls, seen = set(), [], []

        async def serve(reader, writer):
            task = asyncio.current_task()
            tasks.add(task)
            try:
                head = await reader.readuntil(b"\r\n\r\n")
                lines = head.split(b"\r\n")
                if lines[0].startswith(b"GET"):
                    writer.write(
                        b"HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\n"
                        b"Connection: close\r\n\r\n"
                    )
                    await writer.drain()
                    return
                headers = {
                    k.lower(): v.strip()
                    for line in lines[1:]
                    if b":" in line
                    for k, v in [line.split(b":", 1)]
                }
                data = await reader.readexactly(int(headers.get(b"content-length", b"0")))
                packet = json.loads(data) if data else {}
                method = packet.get("method", "DELETE")
                seen.append(method)
                code, reply = 200, None
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
                                "description": "needle",
                                "inputSchema": {"type": "object"},
                            }
                        ]
                    }
                elif method == "tools/call":
                    calls.append(packet)
                    code = status
                    reply = {
                        "jsonrpc": "2.0",
                        "id": packet["id"],
                        "error": {"code": -32602, "message": "repair argument"},
                    }
                else:
                    assert method in {"notifications/initialized", "DELETE"}
                    code = 204
                if code == 200:
                    reply = {"jsonrpc": "2.0", "id": packet["id"], "result": result}
                body = json.dumps(reply).encode() if reply is not None else b""
                writer.write(
                    f"HTTP/1.1 {code} Fixture\r\nContent-Type: application/json\r\n"
                    f"Mcp-Session-Id: fixture\r\nContent-Length: {len(body)}\r\n"
                    "Connection: close\r\n\r\n".encode()
                    + body
                )
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                tasks.discard(task)

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        carrier = httpx.AsyncHTTPTransport()
        context = MCPRuntimeContext((MCPHTTPEnvironment("remote", carrier),)) if bound else None
        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            api_mode="responses",
            tool_search_mode=mode,
            model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
            mcp_servers=(
                MCPServerSettings(
                    "docs",
                    "http",
                    url=f"http://127.0.0.1:{port}",
                    environment_id="remote" if bound else "local",
                ),
            ),
        )
        name = "mcp__docs::read"

        class Model:
            count = 0

            async def stream(self, request):
                self.count += 1
                turn, step = request.items[-1].turn_id, new_step_id()
                if self.count == 1:
                    assert name not in [tool.name for tool in request.tools]
                    call = ToolCall(new_tool_call_id(), "tool_search", {"query": "needle"})
                elif self.count == 2:
                    search = [item for item in request.items if isinstance(item, ToolResultItem)][
                        -1
                    ]
                    assert [tool.name for tool in search.discovered_tools] == [name]
                    assert name in {tool.name for tool in request.tools}
                    call = ToolCall(new_tool_call_id(), name, {})
                else:
                    result = [item for item in request.items if isinstance(item, ToolResultItem)][
                        -1
                    ]
                    assert result.is_error and "MCP error -32602: repair argument" in result.content
                    assert len(calls) == 1
                    yield ModelCompleted((AssistantMessageItem("handled", turn, step),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, step),))

            async def aclose(self):
                pass

        model = Model()

        def create(thread=None):
            return LangGraphRuntime.create(
                settings=settings,
                model=model,
                registry=ToolRegistry(),
                database_path=tmp_path / "history.db",
                home_path=tmp_path / "home",
                thread_id=thread,
                mcp_runtime_context=context,
            )

        runtime = create()
        try:
            try:
                events = [e async for e in runtime.stream("needle")]
                assert isinstance(events[-1], TurnCompleted), events[-1]
                raw = await runtime._repository.load_items(runtime._thread_id)
            finally:
                await runtime.aclose()
            cold = create(runtime._thread_id)
            try:
                events = [e async for e in cold.stream("continue")]
                assert isinstance(events[-1], TurnCompleted), events[-1]
                after = await cold._repository.load_items(cold._thread_id)
                assert after[: len(raw)] == raw and len(calls) == 1
            finally:
                await cold.aclose()
            assert model.count == 4
            assert seen == [
                "initialize",
                "notifications/initialized",
                "tools/list",
                "tools/call",
                "DELETE",
                "initialize",
                "notifications/initialized",
                "tools/list",
                "DELETE",
            ]
        finally:
            await carrier.aclose()
            server.close()
            await server.wait_closed()
            await asyncio.gather(*tasks)

    asyncio.run(scenario())
