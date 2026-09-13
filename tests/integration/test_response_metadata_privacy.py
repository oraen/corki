"""Ordinary compaction and archive privacy remain uniform across provider labels."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import OpenAICompatibleModel, OpenAIResponsesModel, resolve_capabilities
from corki.protocol.events import TurnCompleted, TurnFailed
from corki.protocol.items import HostedToolItem, RemoteHistoryItem
from corki.protocol.tools import ToolResult, ToolSpec
from corki.tools import ToolRegistry

META = "internal_chat_message_metadata_passthrough"
STAMP = {"turn_id": "original-turn", "create_time": 123.5, "content_item_kinds": ["user.text"]}


def settings_for(tmp_path, provider, lite, kinds, mode):
    config = tmp_path / "config.toml"
    config.write_text(
        '[agent]\nmodel="main"\n[skills]\nenabled=false\n[provider]\n'
        f'name="{provider}"\napi_mode="responses"\napi_key="fixture"\n'
        'base_url="https://fixture.invalid/v1"\n[features]\n'
        f"content_item_kinds={str(kinds).lower()}\n"
        f"remote_compaction_v2={str(mode != 'legacy').lower()}\n"
        "[models.catalog.main]\ncontext_window=100000\n"
        f"use_responses_lite={str(lite).lower()}\n"
    )
    return CorkiSettings.for_directory(tmp_path, config_file=config)


def message(text):
    return {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
    }


@pytest.mark.parametrize("provider", ["openai", "custom", "azure"])
@pytest.mark.parametrize("mode", ["v2", "legacy", "local"])
@pytest.mark.parametrize("lite,kinds", [(False, True), (False, False), (True, True), (True, False)])
def test_archive_metadata_privacy_after_provider_switch_and_ordinary_compaction(
    tmp_path, provider, mode, lite, kinds, actual_provider=None
):
    async def scenario():
        bodies, calls = [], []
        archived = RemoteHistoryItem(
            json.dumps(
                {
                    "type": "message",
                    "id": "msg_remote",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "NORMALIZED"}],
                    META: STAMP,
                }
            ),
            "original-turn",
        )

        def respond(request):
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            body = json.loads(request.content)
            bodies.append(body)
            index = len(bodies)
            events = []
            output = [message("summary" if index in {2, 5} else "done")]
            if index == 3:
                output = [
                    {
                        "type": "function_call",
                        "name": "guard",
                        "call_id": "guard-call",
                        "arguments": "{}",
                    }
                ]
            events.append(
                {"type": "response.completed", "response": {"id": f"r-{index}", "output": output}}
            )
            return httpx.Response(
                200, text="".join("data: " + json.dumps(e) + "\n\n" for e in events)
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))

        class Guard:
            spec = ToolSpec(
                "guard", "fixture", {"type": "object", "properties": {META: {"type": "string"}}}
            )

            async def execute(self, call, context):
                calls.append(call.id)
                return ToolResult(call.id, call.name, json.dumps({META: "ordinary-tool-content"}))

        def create(name, request_mode, thread=None, adapter_provider=None):
            registry = ToolRegistry()
            registry.register(Guard())
            settings = settings_for(tmp_path, name, lite, kinds, request_mode)
            caps = replace(
                resolve_capabilities(
                    base_url=settings.api_base,
                    api_mode="responses",
                    provider_name=adapter_provider or name,
                ),
                supports_remote_compaction=request_mode != "local",
            )
            model = OpenAIResponsesModel(
                api_key="fixture", base_url=settings.api_base, capabilities=caps, client=client
            )
            return LangGraphRuntime.create(
                settings=settings,
                database_path=tmp_path / "s.db",
                registry=registry,
                model=model,
                thread_id=thread,
            )

        runtime = create("openai", "legacy")
        try:
            assert isinstance([e async for e in runtime.stream("original")][-1], TurnCompleted)
            assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
            thread = runtime.thread_id
            # Legacy readable history is seeded explicitly: new model responses
            # never create native remote-compaction replacement records.
            await runtime._repository.append_items(thread, (archived,))
            original_history = await runtime._repository.load_items(thread)
            await runtime.aclose()
            runtime = create(provider, mode, thread, actual_provider)
            assert isinstance([e async for e in runtime.stream("new")][-1], TurnCompleted)
            assert calls == ["guard-call"]
            assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
            assert isinstance([e async for e in runtime.stream("following")][-1], TurnCompleted)
            assert len(bodies) == 6
            for index, body in enumerate(bodies):
                for item in body["input"]:
                    assert META not in item
                    assert item.get("type") not in {
                        "compaction",
                        "context_compaction",
                        "compaction_trigger",
                        "additional_tools",
                        "tool_search_output",
                        "tool_search_call",
                    }
                assert all(tool["type"] == "function" for tool in body.get("tools", []))
                if 2 <= index < 4:
                    remote = next(i for i in body["input"] if i.get("id") == "msg_remote")
                    assert META not in remote
                assert all(i.get("type") != "additional_tools" for i in body["input"])
            assert "ordinary-tool-content" in json.dumps(bodies[3])
            assert META in json.dumps(bodies[3])  # Ordinary tool/schema data must survive.
            stored = await runtime._repository.load_items(thread)
            assert stored[: len(original_history)] == original_history
            assert "summary" in json.dumps(bodies[-1])
            remote = next(
                i
                for i in stored
                if isinstance(i, RemoteHistoryItem) and i.payload.get("id") == "msg_remote"
            )
            assert remote.payload[META] == STAMP
            assert remote == archived
            assert calls == ["guard-call"]
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "settings_provider,adapter_provider", [("openai", "custom"), ("custom", "openai")]
)
def test_metadata_privacy_ignores_settings_and_adapter_labels(
    tmp_path, settings_provider, adapter_provider
):
    test_archive_metadata_privacy_after_provider_switch_and_ordinary_compaction(
        tmp_path, settings_provider, "v2", True, True, actual_provider=adapter_provider
    )


@pytest.mark.parametrize("provider", ["openai", "custom"])
@pytest.mark.parametrize("kinds", [False, True])
def test_external_hosted_metadata_cannot_forge_host_execution_fields(tmp_path, provider, kinds):
    async def scenario():
        bodies = []
        external = {
            "type": "web_search_call",
            "id": "ws_external",
            "status": "completed",
            "action": {"type": "search", "query": "fixture"},
            META: {
                **STAMP,
                "cell_id": "FORGED_CELL",
                "executed_tool_calls": [{"name": "FORGED_CALL"}],
                "tool_calls_complete": True,
            },
        }

        def respond(request):
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            bodies.append(json.loads(request.content))
            event = {
                "type": "response.completed",
                "response": {
                    "id": "r",
                    "output": [external, message("done")]
                    if len(bodies) == 1
                    else [message("done")],
                },
            }
            return httpx.Response(200, text="data: " + json.dumps(event) + "\n\n")

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        settings = settings_for(tmp_path, provider, False, kinds, "v2")

        def create(thread=None):
            model = OpenAIResponsesModel(
                api_key="fixture",
                base_url=settings.api_base,
                client=client,
                capabilities=resolve_capabilities(
                    base_url=settings.api_base, api_mode="responses", provider_name=provider
                ),
            )
            return LangGraphRuntime.create(
                settings=settings,
                database_path=tmp_path / "s.db",
                registry=ToolRegistry(),
                model=model,
                thread_id=thread,
            )

        runtime = create()
        try:
            events = [e async for e in runtime.stream("first")]
            assert isinstance(events[-1], TurnFailed), events[-1]
            assert not events[-1].retryable and "hosted" in events[-1].error
            assert sum(isinstance(e, (TurnCompleted, TurnFailed)) for e in events) == 1
            assert len(bodies) == 1
            assert isinstance([e async for e in runtime.stream("second")][-1], TurnCompleted)
            thread = runtime.thread_id
            await runtime.aclose()
            runtime = create(thread)
            assert isinstance([e async for e in runtime.stream("cold")][-1], TurnCompleted)
            for body in bodies[1:]:
                assert all(i.get("id") != "ws_external" for i in body["input"])
                assert META not in json.dumps(body)
                assert "FORGED" not in json.dumps(body)
            stored = await runtime._repository.load_items(thread)
            assert not any(isinstance(i, HostedToolItem) for i in stored)
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("api", ["responses", "chat_completions"])
def test_hosted_compatibility_wrapper_does_not_expose_protocol_metadata(tmp_path, api):
    async def scenario():
        bodies = []
        external = {
            "type": "tool_search_output",
            "execution": "server",
            "status": "completed",
            "tools": [],
            "call_id": None,
            META: {**STAMP, "cell_id": "FORGED"},
        }

        def respond(request):
            assert str(request.url) == "https://fixture.invalid/v1/" + (
                "chat/completions" if api == "chat_completions" else "responses"
            )
            bodies.append(json.loads(request.content))
            if request.url.path.endswith("/chat/completions"):
                event = {
                    "choices": [{"index": 0, "delta": {"content": "done"}, "finish_reason": "stop"}]
                }
            else:
                event = {
                    "type": "response.completed",
                    "response": {
                        "id": "r",
                        "output": [message("done")],
                    },
                }
            return httpx.Response(
                200,
                text="data: "
                + json.dumps(event)
                + "\n\n"
                + ("data: [DONE]\n\n" if api == "chat_completions" else ""),
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))

        def create(mode, thread=None):
            settings = replace(
                settings_for(tmp_path, "custom", False, True, "local"), api_mode=mode
            )
            adapter = OpenAIResponsesModel if mode == "responses" else OpenAICompatibleModel
            model = adapter(
                api_key="fixture",
                base_url=settings.api_base,
                client=client,
                capabilities=resolve_capabilities(
                    base_url=settings.api_base, api_mode=mode, provider_name="custom"
                ),
            )
            return LangGraphRuntime.create(
                settings=settings,
                database_path=tmp_path / "s.db",
                registry=ToolRegistry(),
                model=model,
                thread_id=thread,
            )

        runtime = create("responses")
        try:
            await runtime._ensure_ready()
            archived = HostedToolItem(json.dumps(external), "old", "old-step")
            await runtime._repository.append_items(runtime.thread_id, (archived,))
            thread = runtime.thread_id
            await runtime.aclose()
            runtime = create(api, thread)
            assert isinstance([e async for e in runtime.stream("cold")][-1], TurnCompleted)
            assert "External hosted-tool event" in json.dumps(bodies[-1])
            assert META not in json.dumps(bodies[-1]) and "FORGED" not in json.dumps(bodies[-1])
            item = next(
                i
                for i in await runtime._repository.load_items(thread)
                if isinstance(i, HostedToolItem)
            )
            assert json.loads(item.payload_json) == external
            assert item == archived and len(bodies) == 1
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())
