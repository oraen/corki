"""Cold model downshift summarizes ordinary history before admitting new input."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from test_mcp_exposure_surfaces import install_http_fixture
from test_responses_lite import message, response

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.models import OpenAIResponsesModel, resolve_capabilities
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted
from corki.tools import ToolRegistry


@pytest.mark.parametrize("supported", [False, True])
def test_cold_downshift_preserves_old_search_and_new_plan_separately(
    tmp_path, monkeypatch, supported
):
    async def scenario():
        _, _, clients, closed = install_http_fixture(monkeypatch)
        bodies = []

        def respond(request):
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            bodies.append(json.loads(request.content))
            if len(bodies) == 1:
                event = {
                    "type": "response.completed",
                    "response": {
                        "id": "old",
                        "output": [message()],
                        "usage": {"input_tokens": 20000, "output_tokens": 0, "total_tokens": 20000},
                    },
                }
                return httpx.Response(200, text="data: " + json.dumps(event) + "\n\n")
            return response([message("SUMMARY_PROOF" if len(bodies) == 2 else "done")])

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        model = OpenAIResponsesModel(
            api_key="fixture",
            base_url="https://fixture.invalid/v1",
            client=client,
            capabilities=replace(
                resolve_capabilities(base_url="https://fixture.invalid/v1", api_mode="responses"),
                supports_native_tool_search=True,
                supports_native_namespaces=True,
                supports_remote_compaction=True,
            ),
        )
        settings = CorkiSettings(
            tmp_path,
            model="large",
            api_mode="responses",
            skills_enabled=False,
            tool_search_mode="native",
            tool_namespace_mode="native",
            turn_metadata_includes_tool_info=True,
            model_contexts=(
                ModelContextInfo(
                    "large", 100000, use_responses_lite=True, supports_search_tool=supported
                ),
                ModelContextInfo(
                    "small", 10000, use_responses_lite=True, supports_search_tool=not supported
                ),
            ),
            mcp_servers=(MCPServerSettings("fixture", "http", url="https://fixture.invalid/mcp"),),
        )

        def create(selected, thread=None):
            return LangGraphRuntime.create(
                settings=selected,
                registry=ToolRegistry(),
                model=model,
                database_path=tmp_path / "s.db",
                thread_id=thread,
            )

        runtime = create(settings)
        try:
            events = [e async for e in runtime.stream("OLD_INPUT")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            thread = runtime.thread_id
            original = await runtime._repository.load_items(thread)
            await runtime.aclose()
            runtime = create(replace(settings, model="small"), thread)
            events = [e async for e in runtime.stream("NEW_INPUT")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert [b["model"] for b in bodies] == ["large", "large", "small"]
            assert "NEW_INPUT" not in json.dumps(bodies[1]["input"])
            assert "NEW_INPUT" in json.dumps(bodies[2]["input"])
            assert "SUMMARY_PROOF" in json.dumps(bodies[2]["input"])
            assert (await runtime._repository.load_items(thread))[: len(original)] == original
            for index, body in enumerate(bodies):
                tools = body.get("tools", [])
                assert all(tool["type"] == "function" for tool in tools)
                assert any(tool.get("name") == "tool_search" for tool in tools) is (index != 1)
                if index == 1:
                    assert tools == []
                assert "client_metadata" not in body
                assert all(item.get("type") != "compaction" for item in body["input"])
            assert len(clients) == 2
        finally:
            await runtime.aclose()
            await client.aclose()
        assert closed == ["fixture", "fixture"]

    asyncio.run(scenario())
