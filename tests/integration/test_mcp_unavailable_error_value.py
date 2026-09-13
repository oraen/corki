"""Selected MCP handlers retain the error-value channel after their route disappears."""

import asyncio
import json
import sqlite3
from dataclasses import replace

import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import MCPClient
from corki.models import ModelCompleted
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("change", ["unchanged", "removed", "disabled", "tool_removed"])
@pytest.mark.parametrize(
    "mode,replay",
    [
        ("code_mode", False),
        ("caught", False),
        ("compatible", False),
        ("native", False),
        ("code_mode", True),
        ("caught", True),
    ],
)
def test_captured_mcp_handler_unavailability_is_a_value_not_a_js_exception(
    tmp_path, monkeypatch, change, mode, replay
):
    async def scenario():
        clients, calls, observations = [], [], []
        definitions = [{"name": "echo", "inputSchema": {"type": "object"}}]
        nested = mode in {"code_mode", "caught"}
        if replay:
            identity = new_tool_call_id()
            monkeypatch.setattr("corki.code_mode.service.new_tool_call_id", lambda: identity)

        class Client(MCPClient):
            async def start(self):
                pass

            async def list_tools(self):
                return tuple(definitions)

            async def call_tool(self, name, arguments):
                calls.append(name)
                return {"content": [{"type": "text", "text": "live result"}]}

            async def aclose(self):
                self.closed = True

            async def _exchange(self, message):
                raise AssertionError(message)

            async def _send_notification(self, message):
                raise AssertionError(message)

        def factory(settings):
            client = Client(settings)
            client.closed = False
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        server = MCPServerSettings("docs", "http", url="https://fixture.invalid")

        class Model:
            calls = 0

            async def stream(self, request):
                self.calls += 1
                turn, step = request.items[-1].turn_id, new_step_id()
                if self.calls == 1 and not nested:
                    call = ToolCall(new_tool_call_id(), "tool_search", {"query": "docs echo"})
                elif self.calls == (1 if nested else 2):
                    if change == "tool_removed":
                        definitions.clear()
                        runtime.request_mcp_refresh()
                    elif change != "unchanged":
                        runtime.request_mcp_reconcile(
                            ()
                            if change == "removed"
                            else (replace(server, disabled_tools=("echo",)),)
                        )
                    code = "text(await tools.mcp__docs__echo({}));"
                    if replay:
                        code = "for(let i=0;i<2;i++){" + code + "}"
                    if mode == "caught":
                        code = "try { " + code + ' } catch (e) { text("caught-unavailable"); }'
                    call = (
                        ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments=code,
                        )
                        if nested
                        else ToolCall(new_tool_call_id(), "mcp__docs::echo", {})
                    )
                else:
                    output = [item for item in request.items if isinstance(item, ToolResultItem)][
                        -1
                    ]
                    observations.append(output)
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, step),))

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                execution_permissions=None,
                mcp_servers=(server,),
                tool_mode="code_mode_only" if nested else "direct",
                tool_search_mode="disabled" if nested else mode,
                api_mode="responses",
                model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "unavailable.db",
            home_path=tmp_path / "home",
        )
        try:
            await runtime._ensure_ready()
            await runtime._mcp_manager.start()
            events = [event async for event in runtime.stream("call echo")]
            assert calls == (["echo"] if change == "unchanged" else [])
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(observations) == 1
            output = observations[0]
            assert output.is_error == (change != "unchanged" and not nested), output.content
            assert "caught-unavailable" not in output.content
            if change != "unchanged" and nested:
                assert '"isError":true' in output.content.replace(" ", "")
            elif change == "unchanged":
                assert "live result" in output.content
            if replay:
                marker = '"isError":true' if change != "unchanged" else "live result"
                assert output.content.replace(" ", "").count(marker.replace(" ", "")) == 2
            with sqlite3.connect(tmp_path / "unavailable.db") as db:
                rows = db.execute(
                    "SELECT status,result_json FROM tool_executions "
                    "WHERE tool_name='mcp__docs::echo'"
                ).fetchall()
            assert len(rows) == 1 and rows[0][0] == "completed"
            payload = json.loads(rows[0][1])
            assert payload["is_error"] == (change != "unchanged")
            assert not payload.get("dispatch_error", False)
        finally:
            await runtime.aclose()
        assert all(client.closed for client in clients)

    asyncio.run(scenario())
