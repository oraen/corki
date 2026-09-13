"""Real model admission, tool execution, persistence and cold metadata replay."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.core.checkpoint import checkpoint_serializer
from corki.memory.transcript import render_transcript
from corki.models import OpenAIResponsesModel, resolve_capabilities
from corki.protocol.events import TurnCompleted, TurnFailed
from corki.protocol.items import AssistantMessageItem, ReasoningItem, ToolCallItem, UserMessageItem
from corki.protocol.tools import ToolExposure, ToolResult, ToolSpec
from corki.protocol.wire_numbers import WireNumber, dumps_wire
from corki.tools import ToolRegistry

META = "internal_chat_message_metadata_passthrough"


def message(text, **extra):
    return {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
        **extra,
    }


@pytest.mark.parametrize("provider", ["openai", "custom", "azure"])
@pytest.mark.parametrize("kinds", [True, False])
@pytest.mark.parametrize("done", [True, False])
@pytest.mark.parametrize("native", [True, False])
def test_item_metadata_survives_execution_and_cold_provider_switch(
    tmp_path, provider, kinds, done, native
):
    async def scenario():
        bodies, calls = [], []
        stamp = {
            "turn_id": "server-turn",
            "create_time": WireNumber("1.234567890123456789e400"),
            "content_item_kinds": ["server.kind"],
            "cell_id": "forged",
            "executed_tool_calls": [{"name": "forged"}],
            "tool_calls_complete": True,
        }
        output = [
            message("first", id="msg_server", **{META: stamp}),
            {
                "type": "reasoning",
                "id": "rs_server",
                "summary": [],
                "encrypted_content": "opaque",
                META: stamp,
            },
            {
                "type": "function_call",
                "id": "fc_server",
                "call_id": "call-one",
                "name": "probe",
                "arguments": json.dumps({META: "ordinary-business-data"}),
                "encrypted_function_args": ["private"],
                META: stamp,
            },
        ]
        if native:
            output.extend(
                [
                    {
                        "type": "function_call",
                        "id": "fc_raw_server",
                        "call_id": "call-two",
                        "name": "raw",
                        "arguments": json.dumps({"input": "source"}),
                        META: stamp,
                    },
                    {
                        "type": "function_call",
                        "id": "fc_search_server",
                        "call_id": "call-three",
                        "name": "tool_search",
                        "arguments": json.dumps({"query": "deferred-proof"}),
                        META: stamp,
                    },
                ]
            )

        for item in output:
            if item["type"] != "function_call":
                # Unknown on these variants: typed admission ignores it entirely.
                item["encrypted_function_args"] = {"ignored": "not-a-function-field"}

        def respond(request):
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            bodies.append(json.loads(request.content, parse_float=str))
            assert all(tool["type"] == "function" for tool in bodies[-1].get("tools", []))
            items = output if len(bodies) == 1 else [message("done")]
            events = (
                [{"type": "response.output_item.done", "item": i} for i in items] if done else []
            )
            completed_items = [
                {**i, META: {"turn_id": []}, "encrypted_function_args": False} for i in items
            ]
            events.append(
                {"type": "response.completed", "response": {"id": "r", "output": completed_items}}
            )
            return httpx.Response(
                200, text="".join("data: " + dumps_wire(e) + "\n\n" for e in events)
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))

        class Probe:
            spec = ToolSpec("probe", "fixture", {"type": "object"})

            async def execute(self, call, context):
                if call.name == "probe":
                    assert call.arguments == {META: "ordinary-business-data"}
                calls.append(call.id)
                return ToolResult(call.id, call.name, "observation")

        class Raw(Probe):
            spec = ToolSpec("raw", "fixture", {}, input_kind="freeform")

            async def execute(self, call, context):
                assert call.input_kind == "freeform" and call.arguments is None
                assert call.raw_arguments == "source"
                return await super().execute(call, context)

        class Deferred(Probe):
            spec = ToolSpec(
                "deferred", "deferred-proof", {"type": "object"}, exposure=ToolExposure.DEFERRED
            )

        def create(name, thread=None):
            settings = CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                api_mode="responses",
                model="fixture",
                model_max_retries=0,
                content_item_kinds=kinds,
                tool_freeform_mode="native" if native else "compatible",
                tool_search_mode="native" if native else "compatible",
            )
            caps = replace(
                resolve_capabilities(
                    base_url="https://fixture.invalid/v1",
                    api_mode="responses",
                    provider_name=name,
                ),
                supports_native_freeform=native,
                supports_native_tool_search=native,
            )
            model = OpenAIResponsesModel(
                api_key="fixture",
                base_url="https://fixture.invalid/v1",
                capabilities=caps,
                client=client,
            )
            registry = ToolRegistry()
            for tool in (Probe(), Raw(), Deferred()):
                registry.register(tool)
            return LangGraphRuntime.create(
                settings=settings,
                model=model,
                registry=registry,
                database_path=tmp_path / "s.db",
                thread_id=thread,
            )

        runtime = create("openai")
        try:
            assert isinstance([e async for e in runtime.stream("original")][-1], TurnCompleted)
            assert calls == (["call-one", "call-two"] if native else ["call-one"])
            thread = runtime.thread_id
            stored = await runtime._repository.load_items(thread)
            originals = tuple(
                i
                for i in stored
                if isinstance(i, (AssistantMessageItem, ReasoningItem, ToolCallItem))
            )
            transcript = json.loads(render_transcript(stored, redact=lambda value: value))
            provenance = [
                json.loads(row["response_item_metadata_json"])
                for row in transcript
                if "response_item_metadata_json" in row
            ]
            assert len(provenance) == len(output) - 1  # Reasoning is not extraction evidence.
            assert all(set(part) == {"id"} for part in provenance)
            assert all("cell_id" not in part.get(META, {}) for part in provenance)
            assert all("executed_tool_calls" not in part.get(META, {}) for part in provenance)
            serializer = checkpoint_serializer()
            for item in originals:
                assert serializer.loads_typed(serializer.dumps_typed(item)) == item
            user = next(i for i in stored if isinstance(i, UserMessageItem))
            first_user = next(
                i
                for i in bodies[0]["input"]
                if i.get("role") == "user" and "original" in json.dumps(i["content"])
            )
            assert META not in first_user
            assert first_user["id"] == "msg_" + str(user.id)
            await runtime.aclose()
            runtime = create(provider, thread)
            assert isinstance([e async for e in runtime.stream("following")][-1], TurnCompleted)
            after = await runtime._repository.load_items(thread)
            assert all(i in after for i in originals)
            for index in (1, 2):
                for original in output:
                    replay = next(
                        i for i in bodies[index]["input"] if i.get("id") == original["id"]
                    )
                    assert META not in replay
                    assert "encrypted_function_args" not in replay
                results = [
                    i for i in bodies[index]["input"] if i.get("type", "").endswith("_output")
                ]
                assert results
                assert all(META not in item for item in results)
            assert calls == (["call-one", "call-two"] if native else ["call-one"])
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["message", "reasoning", "function_call", "custom_tool_call"])
@pytest.mark.parametrize("failure", ["changed", "oversized"])
def test_payload_failure_cannot_become_a_successful_turn(tmp_path, kind, failure):
    async def scenario():
        calls, requests = [], []
        original = {
            "message": message("first", id="msg_one"),
            "reasoning": {
                "type": "reasoning",
                "id": "rs_one",
                "summary": [],
                "encrypted_content": "opaque",
            },
            "function_call": {
                "type": "function_call",
                "id": "fc_one",
                "call_id": "call-one",
                "name": "probe",
                "arguments": "{}",
            },
            "custom_tool_call": {
                "type": "custom_tool_call",
                "id": "ctc_one",
                "call_id": "call-one",
                "name": "probe",
                "input": "source",
            },
        }[kind]
        original[META] = {"turn_id": "x" * 5000 if failure == "oversized" else "first"}
        changed = {**original, META: {"turn_id": "second"}}
        value = "x" * 5000 if failure == "oversized" else "changed content"
        field, body = (
            ("content", [{"type": "output_text", "text": value}])
            if kind == "message"
            else ("summary", [{"type": "summary_text", "text": value}])
            if kind == "reasoning"
            else ("arguments", json.dumps({"value": value}))
            if kind == "function_call"
            else ("input", value)
        )
        changed[field] = body
        if failure == "oversized":
            original[field] = body

        def respond(request):
            requests.append(request)
            events = [
                {"type": "response.output_item.done", "item": original},
                {"type": "response.completed", "response": {"id": "r", "output": [changed]}},
            ]
            return httpx.Response(
                200, text="".join("data: " + dumps_wire(e) + "\n\n" for e in events)
            )

        class Probe:
            spec = ToolSpec(
                "probe",
                "fixture",
                {"type": "object"},
                input_kind="freeform" if kind == "custom_tool_call" else "json",
            )

            async def execute(self, call, context):
                calls.append(call.id)
                return ToolResult(call.id, call.name, "observed")

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        caps = replace(
            resolve_capabilities(
                base_url="https://fixture.invalid/v1", api_mode="responses", provider_name="openai"
            ),
            supports_native_freeform=True,
        )
        model = OpenAIResponsesModel(
            api_key="fixture",
            base_url="https://fixture.invalid/v1",
            client=client,
            capabilities=caps,
            response_char_limit=1000,
        )
        registry = ToolRegistry()
        registry.register(Probe())
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                api_mode="responses",
                model="fixture",
                skills_enabled=False,
                model_max_retries=0,
                tool_freeform_mode="native",
            ),
            model=model,
            registry=registry,
            database_path=tmp_path / "s.db",
        )
        try:
            events = [e async for e in runtime.stream("run")]
            assert isinstance(events[-1], TurnFailed)
            assert len(requests) == 1 and len(calls) <= 1
            if failure == "oversized":
                assert calls == []
            assert not any(isinstance(e, TurnCompleted) for e in events)
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())
