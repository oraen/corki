"""Real provider adapters with offline Responses/Chat discovery and namespace wire."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.mcp.tools import MCPTool
from corki.models import OpenAICompatibleModel, OpenAIResponsesModel, resolve_capabilities
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted
from corki.protocol.tool_names import compatible_tool_name
from corki.protocol.tools import ToolExposure, ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mcp", [False, "described", "empty"])
@pytest.mark.parametrize(
    "provider,base_url,model_name",
    [
        ("openai", "https://api.openai.com/v1", "gpt-5"),
        ("openai", "https://fixture.invalid/v1", "fixture-model"),
        ("fixture", "https://api.openai.com/v1", "fixture-model"),
        ("fixture", "https://fixture.invalid/v1", "gpt-5"),
    ],
)
@pytest.mark.parametrize(
    "api,native_search,native_namespace",
    [
        ("responses", True, True),
        ("responses", False, True),
        ("responses", False, False),
        ("chat_completions", False, False),
    ],
)
def test_namespace_hint_search_load_call_observation_on_provider_wire(
    tmp_path,
    api,
    native_search,
    native_namespace,
    mcp,
    provider,
    base_url,
    model_name,
):
    async def scenario():
        bodies, calls = [], []
        namespace = "mcp__vault" if mcp else "vault"

        class Read:
            spec = ToolSpec(
                "vault::read",
                "vaultproof",
                {"type": "object"},
                exposure=ToolExposure.DEFERRED,
                namespace_description="Private & recovery",
                source="SOURCE DIRECTORY",
            )

            async def execute(self, call, context):
                calls.append(call.name)
                return ToolResult(call.id, call.name, "RECOVERED")

        class MCPClient:
            async def call_tool(self, name, arguments):
                assert name == "read" and arguments == {}
                calls.append(spec.name)
                return {"content": [{"type": "text", "text": "RECOVERED"}]}

        tool = (
            MCPTool(
                "vault",
                {"name": "read", "description": "vaultproof", "inputSchema": {"type": "object"}},
                MCPClient(),
                exposure=ToolExposure.DEFERRED,
                server_instructions=None if mcp == "empty" else "Private & recovery",
            )
            if mcp
            else Read()
        )
        spec = tool.spec

        def respond(request):
            assert str(request.url) == base_url + "/" + (
                "responses" if api == "responses" else "chat/completions"
            )
            assert request.headers["authorization"] == "Bearer fixture"
            assert not any(
                key.startswith(("x-codex-", "x-openai-"))
                or key in {"chatgpt-account-id", "openai-beta"}
                for key in request.headers
            )
            body = json.loads(request.content)
            assert body["model"] == model_name
            bodies.append(body)
            index = len(bodies)
            if index <= 2:
                name = "tool_search" if index == 1 else compatible_tool_name(spec.name)
                arguments = {"query": "vaultproof"} if index == 1 else {}
                if api == "responses":
                    item = {
                        "type": "function_call",
                        "name": name,
                        "arguments": json.dumps(arguments),
                    }
                    item.update(id=f"item-{index}", call_id=f"call-{index}")
                else:
                    delta = {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": f"call-{index}",
                                "type": "function",
                                "function": {"name": name, "arguments": json.dumps(arguments)},
                            }
                        ]
                    }
            elif api == "responses":
                item = {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "done"}],
                }
            else:
                delta = {"content": "done"}
            if api == "responses":
                packet = {
                    "type": "response.completed",
                    "response": {"id": f"r-{index}", "output": [item]},
                }
            else:
                packet = {
                    "choices": [
                        {
                            "index": 0,
                            "delta": delta,
                            "finish_reason": "tool_calls" if index <= 2 else "stop",
                        }
                    ]
                }
            return httpx.Response(200, text=f"data: {json.dumps(packet)}\n\n")

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        adapter = OpenAIResponsesModel if api == "responses" else OpenAICompatibleModel
        model = adapter(
            api_key="fixture",
            base_url=base_url,
            client=client,
            capabilities=replace(
                resolve_capabilities(base_url=base_url, api_mode=api, provider_name=provider),
                supports_native_tool_search=native_search,
                supports_native_namespaces=native_namespace,
            ),
        )
        registry = ToolRegistry()
        registry.register(tool)
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                deferred_tool_world_state=True,
                api_mode=api,
                api_base=base_url,
                api_key="fixture",
                provider_name=provider,
                model=model_name,
                tool_search_mode="native" if native_search else "compatible",
                model_contexts=(ModelContextInfo(model_name, supports_search_tool=True),),
                tool_namespace_mode="native" if native_namespace else "compatible",
            ),
            registry=registry,
            model=model,
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
        )
        try:
            events = [e async for e in runtime.stream("read")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(bodies) == 3 and calls == [spec.name]
            for body in bodies:
                messages = body["input" if api == "responses" else "messages"]
                catalogs = [m for m in messages if "<tools>" in json.dumps(m.get("content", ""))]
                # The existing Chat compatibility adapter maps developer to system;
                # Responses preserves the native developer role.
                assert len(catalogs) == 1
                assert catalogs[0]["role"] == ("developer" if api == "responses" else "system")
                assert f"- {namespace}" in json.dumps(catalogs[0])
                assert ("Private &amp; recovery" in json.dumps(catalogs[0])) == (mcp != "empty")
                assert all(
                    t["type"] == "function" and "namespace" not in t for t in body.get("tools", [])
                )
                if api == "responses":
                    assert not any(
                        i.get("type") == "tool_search_output" or "namespace" in i for i in messages
                    )
                assert "snapshot_state" not in json.dumps(body)
                tool_json = json.dumps(body.get("tools", []))
                assert "SOURCE DIRECTORY" not in tool_json
            assert "vaultproof" not in json.dumps(bodies[0].get("tools", []))
            assert "vaultproof" in json.dumps(bodies[1]["tools"])
            assert "RECOVERED" in json.dumps(bodies[2])
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())
