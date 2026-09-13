"""Structured history bodies survive ordinary replay without flattening to UI text."""

import asyncio
import base64
import io
import json
import wave
from dataclasses import replace

import httpx
import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.media.audio import UNSUPPORTED_INPUT
from corki.models import OpenAICompatibleModel, OpenAIResponsesModel, resolve_capabilities
from corki.protocol.events import AssistantMessageCompleted, TurnCompleted, TurnFailed
from corki.protocol.items import AssistantMessageItem, ReasoningItem
from corki.protocol.tools import ToolResult, ToolSpec
from corki.tools import ToolRegistry

META = "internal_chat_message_metadata_passthrough"
PNG = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg=="
)
CITATION = (
    "<corki-memory-citation><citation_entries>MEMORY.md:1-2|note=[fixture]"
    "</citation_entries><thread_ids>00000000-0000-0000-0000-000000000001"
    "</thread_ids></corki-memory-citation>"
)


def audio_url():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\0\0" * 80)
    return "data:audio/wav;base64," + base64.b64encode(buffer.getvalue()).decode()


def message(text):
    return {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
    }


def stream_response(output, *, done=False):
    events = (
        [{"type": "response.output_item.done", "item": item} for item in output] if done else []
    )
    events.append({"type": "response.completed", "response": {"id": "response", "output": output}})
    return httpx.Response(
        200, text="".join("data: " + json.dumps(event) + "\n\n" for event in events)
    )


@pytest.mark.parametrize("mode", ["responses", "chat", "v2", "legacy", "local"])
@pytest.mark.parametrize("encrypted", [True, False])
@pytest.mark.parametrize("done", [True, False])
@pytest.mark.parametrize("media", [True, False])
def test_body_survives_tool_step_compaction_and_cold_replay(tmp_path, mode, encrypted, done, media):
    async def scenario():
        bodies, calls = [], []
        parts = [{"type": "output_text", "text": "alpha"}, {"type": "output_text", "text": ""}]
        if media:
            parts.extend(
                [
                    {"type": "input_image", "image_url": PNG, "detail": "high"},
                    {"type": "input_audio", "audio_url": audio_url()},
                    {"type": "input_text", "text": "MODEL_ONLY_CONTEXT"},
                ]
            )
        parts.append({"type": "output_text", "text": "beta" + CITATION})
        assistant = {
            **message("unused"),
            "id": "msg_body",
            "content": parts,
            META: {"content_item_kinds": [f"fixture.part{i}" for i in range(len(parts))]},
        }
        reasoning = {
            "type": "reasoning",
            "id": "rs_body",
            "summary": [
                {"type": "summary_text", "text": "first summary"},
                {"type": "summary_text", "text": "second summary"},
            ],
            "content": [
                {"type": "reasoning_text", "text": "raw thought"},
                {"type": "text", "text": "legacy content"},
            ],
            "encrypted_content": "opaque" if encrypted else None,
        }
        output = [
            assistant,
            reasoning,
            {
                "type": "function_call",
                "id": "fc_body",
                "name": "probe",
                "call_id": "one-call",
                "arguments": "{}",
            },
        ]
        compaction = mode in {"v2", "legacy", "local"}

        def respond(request):
            assert str(request.url) in {
                "https://fixture.invalid/v1/responses",
                "https://fixture.invalid/v1/chat/completions",
            }
            body = json.loads(request.content)
            bodies.append(body)
            if request.url.path.endswith("/chat/completions"):
                event = {
                    "choices": [{"index": 0, "delta": {"content": "done"}, "finish_reason": "stop"}]
                }
                return httpx.Response(
                    200, text="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n"
                )
            if len(bodies) == 3 and compaction:
                return stream_response([message("summary")])
            return stream_response(output if len(bodies) == 1 else [message("done")], done=done)

        class Probe:
            spec = ToolSpec("probe", "fixture", {"type": "object"})

            async def execute(self, call, context):
                calls.append(call.id)
                return ToolResult(call.id, call.name, "observed")

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))

        def create(thread=None, chat=False):
            caps = replace(
                resolve_capabilities(
                    base_url="https://fixture.invalid/v1",
                    api_mode="chat_completions" if chat else "responses",
                    provider_name="openai",
                ),
                supports_remote_compaction=mode != "local",
            )
            cls = OpenAICompatibleModel if chat else OpenAIResponsesModel
            model = cls(
                api_key="fixture",
                base_url="https://fixture.invalid/v1",
                client=client,
                capabilities=caps,
            )
            registry = ToolRegistry()
            registry.register(Probe())
            return LangGraphRuntime.create(
                settings=CorkiSettings(
                    working_directory=tmp_path,
                    skills_enabled=False,
                    model="fixture",
                    api_mode="chat_completions" if chat else "responses",
                    remote_compaction_v2=mode != "legacy",
                    model_max_retries=0,
                    context_window_tokens=100000,
                    auto_compact_tokens=90000,
                ),
                model=model,
                registry=registry,
                database_path=tmp_path / "s.db",
                thread_id=thread,
            )

        runtime = create()
        try:
            events = [event async for event in runtime.stream("original")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            visible = [e.text for e in events if isinstance(e, AssistantMessageCompleted)]
            assert visible == ["alphabeta", "done"]
            assert calls == ["one-call"]
            if compaction:
                assert isinstance([event async for event in runtime.compact()][-1], TurnCompleted)
            thread = runtime.thread_id
            before_reopen = await runtime._repository.load_items(thread)
            await runtime.aclose()
            runtime = create(thread, chat=mode == "chat")
            assert isinstance(
                [event async for event in runtime.stream("following")][-1], TurnCompleted
            )
            expected_parts = [
                {"type": "input_text", "text": UNSUPPORTED_INPUT}
                if part["type"] == "input_audio"
                else part
                for part in parts
            ]
            for body in bodies[1:3]:
                if "input" not in body:
                    continue
                replay = next(item for item in body["input"] if item.get("id") == "msg_body")
                assert replay["content"] == expected_parts
                assert META not in replay
                thought = next(item for item in body["input"] if item.get("id") == "rs_body")
                assert thought["type"] == "reasoning"
                assert thought["summary"] == reasoning["summary"]
                assert thought["content"] == reasoning["content"]
                assert thought.get("encrypted_content") == reasoning["encrypted_content"]
            if mode == "chat":
                chat_body = json.dumps(bodies[-1])
                assert "alphabeta" + CITATION in chat_body
                if media:
                    assert PNG in chat_body and "MODEL_ONLY_CONTEXT" in chat_body
            archive = await runtime._repository.load_items(thread)
            assert archive[: len(before_reopen)] == before_reopen
            assert len(bodies) == (4 if compaction else 3)
            if compaction:
                assert "summary" in json.dumps(bodies[-1])
                assert not any(i.get("id") == "msg_body" for i in bodies[-1]["input"])
            saved_assistant = next(
                i
                for i in archive
                if isinstance(i, AssistantMessageItem) and i.content == "alphabeta"
            )
            saved_reasoning = next(i for i in archive if isinstance(i, ReasoningItem))
            assert json.loads(saved_assistant.response_body_json)["content"] == parts
            assert json.loads(saved_assistant.response_item_metadata_json) == {"id": "msg_body"}
            assert json.loads(saved_reasoning.response_body_json) == {
                "summary": reasoning["summary"],
                "content": reasoning["content"],
            }
            assert saved_assistant.memory_citation is not None
            assert calls == ["one-call"]
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["message", "reasoning"])
@pytest.mark.parametrize("failure", ["changed_parts", "hidden_oversize"])
def test_body_structure_and_hidden_size_cannot_bypass_completion_guards(tmp_path, kind, failure):
    async def scenario():
        if kind == "message":
            original = {**message("ab"), "id": "msg_body"}
            original["content"].append({"type": "output_text", "text": "cd"})
            changed = {
                **original,
                "content": [
                    {"type": "output_text", "text": "a"},
                    {"type": "output_text", "text": "bcd"},
                ],
            }
            if failure == "hidden_oversize":
                original["content"].append({"type": "input_text", "text": "x" * 5000})
        else:
            original = {
                "type": "reasoning",
                "id": "rs_body",
                "summary": [
                    {"type": "summary_text", "text": "ab"},
                    {"type": "summary_text", "text": "cd"},
                ],
                "content": [{"type": "reasoning_text", "text": "original"}],
                "encrypted_content": "opaque",
            }
            changed = {**original, "content": [{"type": "reasoning_text", "text": "changed"}]}
            if failure == "hidden_oversize":
                original["content"] = [{"type": "reasoning_text", "text": "x" * 5000}]

        def respond(request):
            events = [
                {"type": "response.output_item.done", "item": original},
                {"type": "response.completed", "response": {"id": "r", "output": [changed]}},
            ]
            return httpx.Response(
                200, text="".join("data: " + json.dumps(e) + "\n\n" for e in events)
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        model = OpenAIResponsesModel(
            api_key="fixture",
            base_url="https://fixture.invalid/v1",
            client=client,
            response_char_limit=1000,
            capabilities=resolve_capabilities(
                base_url="https://fixture.invalid/v1", api_mode="responses", provider_name="openai"
            ),
        )
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                model="fixture",
                skills_enabled=False,
                model_max_retries=0,
            ),
            model=model,
            registry=ToolRegistry(),
            database_path=tmp_path / "s.db",
        )
        try:
            events = [event async for event in runtime.stream("run")]
            assert isinstance(events[-1], TurnFailed)
            assert events[-1].error_kind == "protocol"
            assert ("changed" if failure == "changed_parts" else "hard limit") in events[-1].error
            archived = [
                item
                for item in await runtime._repository.load_items(runtime.thread_id)
                if isinstance(item, (AssistantMessageItem, ReasoningItem))
            ]
            if failure == "hidden_oversize":
                assert archived == []
            else:
                assert len(archived) == 1
                body = json.loads(archived[0].response_body_json)
                assert body["content"] == original["content"]
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())
