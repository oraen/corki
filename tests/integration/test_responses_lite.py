"""Legacy Lite flags keep Runtime discovery, restart and compaction on ordinary HTTP."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from corki import http_client
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import OpenAIResponsesModel, resolve_capabilities
from corki.protocol.events import TurnCompleted
from corki.protocol.tool_names import compatible_tool_name
from corki.protocol.tools import ImageAttachment, TextContent, ToolExposure, ToolResult, ToolSpec
from corki.tools import ToolRegistry

HEADER = "x-openai-internal-codex-responses-lite"
PNG = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg=="
)


def settings_for(tmp_path, lite, *, mode="v2"):
    config = tmp_path / "config.toml"
    config.write_text(
        '[agent]\nmodel="main"\n[skills]\nenabled=false\n[provider]\n'
        'api_mode="responses"\napi_key="fixture"\nbase_url="https://fixture.invalid/v1"\n'
        + f'name="{"custom" if mode == "local" else "openai"}"\n'
        + "[models.catalog.main]\ncontext_window=100000\nsupports_search_tool=true\n"
        + f"use_responses_lite={str(lite).lower()}\n"
    )
    return replace(
        CorkiSettings.for_directory(tmp_path, config_file=config),
        remote_compaction_v2=mode != "legacy",
    )


def response(items=()):
    events = []
    events.append({"type": "response.completed", "response": {"id": "r", "output": list(items)}})
    return httpx.Response(200, text="".join("data: " + json.dumps(e) + "\n\n" for e in events))


def message(text="done"):
    return {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
    }


def check_projection(body, headers, lite):
    assert HEADER not in headers
    assert "context" not in body.get("reasoning", {})
    assert "instructions" in body
    assert not any(i.get("type") == "additional_tools" for i in body["input"])
    assert all(t["type"] == "function" for t in body.get("tools", []))
    return body.get("tools", [])


@pytest.mark.parametrize("lite", [False, True])
@pytest.mark.parametrize(
    "native_search,native_namespace", [(True, True), (False, True), (False, False)]
)
def test_lite_discovery_observation_and_cold_thread_prefix(
    tmp_path, lite, native_search, native_namespace
):
    async def scenario():
        bodies, headers, calls = [], [], []

        class Read:
            spec = ToolSpec(
                "vault::read", "vaultproof", {"type": "object"}, exposure=ToolExposure.DEFERRED
            )

            async def execute(self, call, context):
                calls.append(call.name)
                return ToolResult(
                    call.id,
                    call.name,
                    "RECOVERED",
                    content_items=(TextContent("RECOVERED"), ImageAttachment(PNG, "high")),
                )

        def respond(request):
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            bodies.append(json.loads(request.content))
            headers.append(request.headers)
            index = len(bodies)
            if index <= 2:
                item = {
                    "type": "function_call",
                    "name": "tool_search" if index == 1 else compatible_tool_name("vault::read"),
                    "arguments": json.dumps({"query": "vaultproof"} if index == 1 else {}),
                }
            else:
                return response([message()])
            item.update(id=f"item-{index}", call_id=f"call-{index}")
            return response([item])

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        model = OpenAIResponsesModel(
            api_key="fixture",
            base_url="https://fixture.invalid/v1",
            client=client,
            capabilities=replace(
                resolve_capabilities(base_url="https://fixture.invalid/v1", api_mode="responses"),
                supports_native_tool_search=native_search,
                supports_native_namespaces=native_namespace,
            ),
        )
        settings = replace(
            settings_for(tmp_path, lite),
            tool_search_mode="native" if native_search else "compatible",
            tool_namespace_mode="native" if native_namespace else "compatible",
        )

        async def create(thread=None):
            registry = ToolRegistry()
            registry.register(Read())
            return await LangGraphRuntime.acreate(
                settings=settings,
                registry=registry,
                model=model,
                database_path=tmp_path / "history.db",
                thread_id=thread,
            )

        runtime = await create()
        try:
            assert isinstance([e async for e in runtime.stream("read")][-1], TurnCompleted)
            assert calls == ["vault::read"] and len(bodies) == 3
            tools = [check_projection(b, h, lite) for b, h in zip(bodies, headers, strict=True)]
            assert "vaultproof" not in json.dumps(tools[0])
            assert "vaultproof" in json.dumps(tools[1])
            assert "RECOVERED" in json.dumps(bodies[2]["input"])
            output = next(
                i
                for i in bodies[2]["input"]
                if i.get("call_id") == "call-2" and i.get("type") == "function_call_output"
            )
            picture = next(p for p in output["output"] if p.get("type") == "input_image")
            assert picture["detail"] == "high"
            assert not any(i.get("type") == "tool_search_output" for i in bodies[1]["input"])
            thread = runtime.thread_id
            stored = await runtime._repository.load_items(thread)
            assert "additional_tools" not in repr(stored)
            result = next(i for i in stored if getattr(i, "tool_name", None) == "vault::read")
            assert any(
                isinstance(p, ImageAttachment) and p.detail == "high" for p in result.content_items
            )
            await runtime.aclose()
            runtime = await create(thread)
            assert isinstance([e async for e in runtime.stream("continue")][-1], TurnCompleted)
            check_projection(bodies[-1], headers[-1], lite)
            assert calls == ["vault::read"]
            recovered = await runtime._repository.load_items(thread)
            assert recovered[: len(stored)] == stored
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("lite", [False, True])
def test_lite_http_retry_keeps_prefix_and_header(tmp_path, monkeypatch, lite):
    async def scenario():
        bodies, headers = [], []

        def respond(request):
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            bodies.append(json.loads(request.content))
            headers.append(request.headers)
            if len(bodies) == 1:
                return httpx.Response(503, json={"error": {"message": "temporary"}})
            return response([message()])

        client = httpx.AsyncClient
        monkeypatch.setattr(
            http_client,
            "OwnedHTTPClient",
            lambda *a, **kw: client(*a, **kw, transport=httpx.MockTransport(respond)),
        )
        runtime = await LangGraphRuntime.acreate(
            settings=replace(settings_for(tmp_path, lite), model_retry_base_seconds=0.001),
            database_path=tmp_path / "s.db",
            registry=ToolRegistry(),
        )
        try:
            assert isinstance([e async for e in runtime.stream("first")][-1], TurnCompleted)
            assert len(bodies) == 2 and bodies[0] == bodies[1]
            for body, header in zip(bodies, headers, strict=True):
                check_projection(body, header, lite)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("lite", [False, True])
@pytest.mark.parametrize("mode", ["v2", "legacy", "local"])
def test_lite_normal_manual_and_following_request_contract(tmp_path, monkeypatch, lite, mode):
    async def scenario():
        bodies, headers = [], []

        def respond(request):
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            bodies.append(json.loads(request.content))
            headers.append(request.headers)
            compact = len(bodies) == 2
            return response([message("summary" if compact else "done")])

        client = httpx.AsyncClient
        monkeypatch.setattr(
            http_client,
            "OwnedHTTPClient",
            lambda *a, **kw: client(*a, **kw, transport=httpx.MockTransport(respond)),
        )
        runtime = await LangGraphRuntime.acreate(
            settings=settings_for(tmp_path, lite, mode=mode),
            database_path=tmp_path / "s.db",
            registry=ToolRegistry(),
        )
        try:
            assert isinstance([e async for e in runtime.stream("first")][-1], TurnCompleted)
            assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
            assert isinstance([e async for e in runtime.stream("following")][-1], TurnCompleted)
            assert len(bodies) == 3
            for b, h in zip(bodies, headers, strict=True):
                check_projection(b, h, lite)
            assert bodies[1]["input"][-1].get("type") != "compaction_trigger"
            assert not bodies[1].get("tools")
            assert "summary" in json.dumps(bodies[-1])
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
