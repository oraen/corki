"""Persistent MCP decisions survive sessions; failed writes retain session consent."""

import asyncio
import json
import tomllib
from dataclasses import replace

import httpx
import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import HttpMCPClient
from corki.models import ModelCompleted
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["compatible", "native", "code_mode"])
@pytest.mark.parametrize("case", ["app", "ordinary", "write_failure", "session"])
def test_persistent_decision_and_write_failure_fallback(tmp_path, monkeypatch, mode, case):
    async def scenario():
        server = "ordinary" if case == "ordinary" else "codex_apps"
        config = tmp_path / "config.toml"
        original = (
            "# preserve this comment and unrelated configuration\n"
            '[unrelated]\nvalue = "keep"\n'
            '[mcp]\napproval_policy = "on-request"\n'
            f'[mcp.servers.{server}]\ntransport = "http"\nurl = "https://fixture.invalid"\n'
        )
        config.write_text(original)
        clients, calls, prompts, observations, endings = [], [], [], [], []
        nested = mode == "code_mode"

        def factory(settings):
            async def respond(request):
                packet = json.loads(request.content)
                if packet["method"] == "notifications/initialized":
                    return httpx.Response(202)
                if packet["method"] == "initialize":
                    result = {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": {"name": "fixture", "version": "1"},
                    }
                elif packet["method"] == "tools/list":
                    result = {
                        "tools": [
                            {
                                "name": "write",
                                "description": "Persistent approval fixture",
                                "inputSchema": {"type": "object"},
                                "annotations": {"readOnlyHint": False},
                                "_meta": {"connector_id": "mail"},
                            }
                        ]
                    }
                else:
                    assert packet["method"] == "tools/call"
                    calls.append(packet["params"])
                    result = {"content": [{"type": "text", "text": "business result"}]}
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": packet["id"], "result": result}
                )

            client = HttpMCPClient(settings, transport=httpx.MockTransport(respond))
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)

        class Model:
            steps = 0

            async def stream(self, request):
                self.steps += 1
                turn, step = request.items[-1].turn_id, new_step_id()
                first_call = 1 if nested else 2
                if self.steps == 1 and not nested:
                    call = ToolCall(
                        new_tool_call_id(), "tool_search", {"query": "Persistent approval fixture"}
                    )
                elif self.steps == first_call or (
                    case == "write_failure" and self.steps == first_call + 1
                ):
                    if self.steps > first_call:
                        observations.append(
                            [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                        )
                    if nested:
                        call = ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments="const t=ALL_TOOLS.find(t=>t.description.includes("
                            '"Persistent approval fixture"));text(await tools[t.name]({}));',
                        )
                    else:
                        found = [i for i in request.items if getattr(i, "discovered_tools", ())][-1]
                        call = ToolCall(new_tool_call_id(), found.discovered_tools[0].name, {})
                else:
                    observations.append(
                        [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    )
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, step),))

            async def aclose(self):
                pass

        for session in range(1 if case == "write_failure" else 2):
            settings = replace(
                CorkiSettings.for_directory(tmp_path, config_file=config),
                execution_permissions=None,
                skills_enabled=False,
                api_mode="responses",
                model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
                tool_search_mode="disabled" if nested else mode,
                tool_mode="code_mode_only" if nested else "direct",
            )
            runtime = LangGraphRuntime.create(
                settings=settings,
                model=Model(),
                registry=ToolRegistry(),
                database_path=tmp_path / f"session-{session}.db",
                home_path=tmp_path / "home",
            )

            async def host(request, runtime=runtime):
                prompts.append(request)
                first = len(prompts) == 1
                if first and case == "write_failure":
                    # Invalid existing TOML must not be overwritten by a grant writer.
                    config.write_text("invalid = [")
                runtime.respond_mcp_elicitation(
                    request.server_name,
                    request.request_id,
                    "accept" if first else "decline",
                    meta={"persist": "session" if case == "session" else "always"},
                )

            runtime.set_mcp_elicitation_handler(host)
            try:
                events = [event async for event in runtime.stream("write after approval")]
                endings.append(events[-1])
            finally:
                await runtime.aclose()

        assert all(client.is_closed for client in clients)
        assert all(isinstance(event, TurnCompleted) for event in endings)
        assert (len(prompts), len(calls)) == ((2, 1) if case == "session" else (1, 2))
        assert len(observations) == 2
        assert "business result" in observations[0].content
        assert ("business result" in observations[1].content) is (case != "session")
        if case == "write_failure":
            assert config.read_text() == "invalid = ["
        elif case == "session":
            assert config.read_text() == original
        else:
            document = tomllib.loads(config.read_text())
            assert document["unrelated"] == {"value": "keep"}
            assert "# preserve this comment" in config.read_text()
            tool = (
                document["apps"]["mail"]["tools"]["write"]
                if case == "app"
                else document["mcp"]["servers"]["ordinary"]["tools"]["write"]
            )
            assert tool["approval_mode"] == "approve"

    asyncio.run(scenario())
