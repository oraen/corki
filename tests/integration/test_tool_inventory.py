"""Legacy Lite inventory opt-ins must not leak dispatch data to the model service."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from test_responses_lite import message, response, settings_for

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import OpenAIResponsesModel, resolve_capabilities
from corki.protocol.events import TurnCompleted
from corki.protocol.tools import ToolExposure, ToolResult, ToolSpec, compatible_tool_name
from corki.tools import ToolRegistry


class Read:
    spec = ToolSpec(
        "vault::read",
        "inventoryproof",
        {"type": "object"},
        exposure=ToolExposure.DEFERRED,
        source="forged-server-label",
    )

    async def execute(self, call, context):
        return ToolResult(call.id, call.name, "READ_PROOF")


def inventory_settings(root, *, lite=True, enabled=True, mode="direct"):
    settings_for(root, lite)
    config = root / "config.toml"
    config.write_text(
        config.read_text()
        + "\n[features.tool_registry]\n"
        + f"turn_metadata_includes_tool_info={str(enabled).lower()}\n"
    )
    return replace(
        CorkiSettings.for_directory(root, config_file=config),
        tool_search_mode="native",
        tool_namespace_mode="native",
        tool_mode=mode,
    )


@pytest.mark.parametrize("enabled,lite", [(True, True), (False, True), (True, False)])
def test_selected_inventory_follows_search_to_observation(tmp_path, enabled, lite):
    async def scenario():
        bodies, headers = [], []

        def respond(request):
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            bodies.append(json.loads(request.content))
            headers.append(request.headers)
            index = len(bodies)
            if index == 1:
                return response(
                    [
                        {
                            "type": "function_call",
                            "name": "tool_search",
                            "call_id": "find",
                            "arguments": '{"query":"inventoryproof"}',
                        }
                    ]
                )
            if index == 2:
                assert compatible_tool_name(Read.spec.name) in {
                    tool["name"] for tool in bodies[-1]["tools"]
                }
                return response(
                    [
                        {
                            "type": "function_call",
                            "name": compatible_tool_name(Read.spec.name),
                            "call_id": "read",
                            "arguments": "{}",
                        }
                    ]
                )
            return response([message()])

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        model = OpenAIResponsesModel(
            api_key="fixture",
            base_url="https://fixture.invalid/v1",
            client=client,
            capabilities=replace(
                resolve_capabilities(base_url="https://fixture.invalid/v1", api_mode="responses"),
                supports_native_tool_search=True,
                supports_native_namespaces=True,
            ),
        )
        registry = ToolRegistry()
        registry.register(Read())
        runtime = LangGraphRuntime.create(
            settings=inventory_settings(tmp_path, lite=lite, enabled=enabled),
            model=model,
            registry=registry,
            database_path=tmp_path / "state.db",
        )
        try:
            assert isinstance([e async for e in runtime.stream("find and read")][-1], TurnCompleted)
            assert len(bodies) == 3 and "READ_PROOF" in json.dumps(bodies[-1]["input"])
            for body, header in zip(bodies, headers, strict=True):
                assert all(tool["type"] == "function" for tool in body["tools"])
                assert all("namespace" not in tool for tool in body["tools"])
                canonical = json.loads(
                    body.get("client_metadata", {}).get("x-codex-turn-metadata", "{}")
                )
                assert "tool_namespaces_info" not in canonical
                compatibility = json.loads(header.get("x-codex-turn-metadata", "{}"))
                assert "tool_namespaces_info" not in compatibility
                assert "x-codex-turn-metadata" not in header
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())
