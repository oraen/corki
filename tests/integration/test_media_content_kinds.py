"""Ordinary media projection preserves order and immutable legacy source archives."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import OpenAIResponsesModel, resolve_capabilities
from corki.protocol.events import TurnCompleted
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    RemoteHistoryItem,
    UserMessageItem,
)
from corki.protocol.tools import AudioAttachment, ImageAttachment, TextContent, ToolResult, ToolSpec
from corki.tools import ToolRegistry

META = "internal_chat_message_metadata_passthrough"
PNG = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg=="
)
AUDIO = "data:audio/mp4;base64,YXVkaW8="


def response(output):
    event = {"type": "response.completed", "response": {"id": "r", "output": output}}
    events = [{"type": "response.output_item.done", "item": item} for item in output]
    return httpx.Response(
        200, text="".join("data: " + json.dumps(item) + "\n\n" for item in [*events, event])
    )


@pytest.mark.parametrize("mode", ["none", "v2", "legacy", "local"])
@pytest.mark.parametrize("shape", ["absent", "short", "exact", "long"])
@pytest.mark.parametrize("supported", [False, True, "images", "audio"])
def test_media_classification_survives_runtime_compaction_and_reopen(
    tmp_path, mode, shape, supported, provider="openai", kinds_enabled=True
):
    async def scenario():
        bodies, calls = [], []
        images_supported = supported is True or supported == "images"
        audio_supported = supported is True or supported == "audio"
        parts = [
            {"type": "input_text", "text": "source before"},
            {"type": "input_image", "image_url": PNG, "detail": "auto"},
            {"type": "input_audio", "audio_url": AUDIO},
            {"type": "input_text", "text": "source after"},
        ]
        kinds = {
            "absent": None,
            "short": ["original.first"],
            "exact": ["original.first", "original.image", "original.audio", "original.last"],
            "long": [
                "original.first",
                "original.image",
                "original.audio",
                "original.last",
                "excess",
            ],
        }[shape]
        metadata = {"turn_id": "source-turn"}
        if kinds is not None:
            metadata["content_item_kinds"] = kinds
        native = {
            "type": "message",
            "id": "msg_remote",
            "role": "user",
            "content": parts,
            META: metadata,
        }
        assistant = {**native, "id": "msg_assistant", "role": "assistant"}
        normal = {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "done"}],
        }

        def respond(request):
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            body = json.loads(request.content)
            bodies.append(body)
            if len(bodies) == 1:
                return response(
                    [
                        assistant,
                        {
                            "type": "function_call",
                            "name": "probe",
                            "call_id": "one",
                            "arguments": "{}",
                        },
                    ]
                )
            return response([normal])

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))

        class Probe:
            spec = ToolSpec("probe", "fixture", {"type": "object"})

            async def execute(self, call, context):
                calls.append(call.id)
                return ToolResult(call.id, call.name, "observed")

        async def create(thread=None):
            registry = ToolRegistry()
            registry.register(Probe())
            caps = replace(
                resolve_capabilities(
                    base_url="https://fixture.invalid/v1",
                    api_mode="responses",
                    provider_name=provider,
                ),
                supports_remote_compaction=mode != "local",
                supports_audio_input=audio_supported,
            )
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    working_directory=tmp_path,
                    skills_enabled=False,
                    model="fixture",
                    supports_image_input=images_supported,
                    supports_audio_input=audio_supported,
                    content_item_kinds=kinds_enabled,
                    remote_compaction_v2=mode != "legacy",
                    context_window_tokens=100000,
                    auto_compact_tokens=90000,
                ),
                model=OpenAIResponsesModel(
                    api_key="fixture",
                    base_url="https://fixture.invalid/v1",
                    capabilities=caps,
                    client=client,
                ),
                registry=registry,
                database_path=tmp_path / "s.db",
                thread_id=thread,
            )

        runtime = await create()
        try:
            await runtime._ensure_ready()
            user = UserMessageItem(
                "source",
                "source-turn",
                content_items=(
                    TextContent("source before"),
                    ImageAttachment(PNG),
                    AudioAttachment(AUDIO),
                    TextContent("source after"),
                ),
            )
            remote = RemoteHistoryItem(payload_json=json.dumps(native), turn_id="source-turn")
            await runtime._repository.append_items(runtime.thread_id, (user, remote))
            assert isinstance([e async for e in runtime.stream("continue")][-1], TurnCompleted)
            original = await runtime._repository.load_items(runtime.thread_id)
            if mode != "none":
                assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
            thread = runtime.thread_id
            await runtime.aclose()
            runtime = await create(thread)
            assert isinstance([e async for e in runtime.stream("following")][-1], TurnCompleted)
            assert calls == ["one"]
            assert len(bodies) == (3 if mode == "none" else 4)
            for body in bodies:
                assert all(META not in item for item in body["input"])
                assert all(item.get("type") != "compaction" for item in body["input"])
            # First next-Step and actual compaction input must both retain source positions.
            for body in bodies[1 : 3 if mode != "none" else None]:
                by_id = {item.get("id"): item for item in body["input"]}
                for identity in (f"msg_{user.id}", "msg_remote", "msg_assistant"):
                    item = by_id[identity]
                    assert META not in item
                    assert item["content"][0] == parts[0]
                    assert item["content"][-1] == parts[-1]
                    assert [part["type"] for part in item["content"]] == [
                        "input_text",
                        "input_image" if images_supported else "input_text",
                        "input_audio" if audio_supported else "input_text",
                        "input_text",
                    ]
                    if images_supported:
                        assert item["content"][1] == {
                            **parts[1],
                            "detail": "high" if identity == f"msg_{user.id}" else "auto",
                        }
                    else:
                        assert "image" in item["content"][1]["text"].lower()
                    if audio_supported:
                        assert item["content"][2] == parts[2]
                    else:
                        assert "audio" in item["content"][2]["text"].lower()
            archive = await runtime._repository.load_items(thread)
            assert archive[: len(original)] == original
            summaries = [item for item in archive if isinstance(item, CompactionItem)]
            assert len(summaries) == (0 if mode == "none" else 1)
            if summaries:
                assert summaries[0].remote_payload_json is None
                assert "done" in json.dumps(bodies[-1]["input"])
            saved = next(
                item
                for item in archive
                if isinstance(item, AssistantMessageItem)
                and item.response_body_json
                and "source before" in item.response_body_json
            )
            assert json.loads(saved.response_body_json)["content"] == parts
            assert json.loads(saved.response_item_metadata_json) == {"id": "msg_assistant"}
            assert next(item for item in archive if item.id == user.id) == user
            assert next(item for item in archive if item.id == remote.id) == remote
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("provider,kinds", [("custom", True), ("openai", False)])
def test_media_classification_respects_request_privacy(tmp_path, provider, kinds):
    test_media_classification_survives_runtime_compaction_and_reopen(
        tmp_path, "v2", "short", False, provider, kinds
    )
