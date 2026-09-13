"""Ordinary summaries retain user text and immutable source lineage."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from corki.config import CorkiSettings
from corki.context.history import active_history
from corki.core import LangGraphRuntime
from corki.media.audio import UNSUPPORTED_INPUT
from corki.media.images import UNSUPPORTED
from corki.models import OpenAIResponsesModel, resolve_capabilities
from corki.protocol.events import TurnCompleted
from corki.protocol.items import AssistantMessageItem, CompactionItem, UserMessageItem
from corki.protocol.tools import AudioAttachment, ImageAttachment, TextContent
from corki.tools import ToolRegistry

PNG = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg=="
)
AUDIO = "data:audio/mp4;base64,YXVkaW8="


@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("supported", [False, True])
@pytest.mark.parametrize("provider,kinds", [("openai", True), ("openai", False), ("custom", True)])
def test_ordinary_summary_preserves_source_text_after_cold_reopen(
    tmp_path, automatic, supported, provider, kinds, boundary=False
):
    async def scenario():
        bodies = []

        def respond(request):
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            body = json.loads(request.content)
            bodies.append(body)
            assert all(
                item.get("type", "message") == "message"
                and "internal_chat_message_metadata_passthrough" not in item
                for item in body["input"]
            )
            normal = {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "ordinary summary"}],
            }
            events = [
                {"type": "response.output_item.done", "item": normal},
                {"type": "response.completed", "response": {"id": "r", "output": [normal]}},
            ]
            return httpx.Response(
                200, text="".join("data: " + json.dumps(event) + "\n\n" for event in events)
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))

        def create(thread=None, *, media=supported, old_v2=True):
            caps = replace(
                resolve_capabilities(
                    base_url="https://fixture.invalid/v1",
                    api_mode="responses",
                    provider_name=provider,
                ),
                supports_remote_compaction=True,
                supports_audio_input=media,
            )
            return LangGraphRuntime.create(
                settings=CorkiSettings(
                    working_directory=tmp_path,
                    model="fixture",
                    api_mode="responses",
                    skills_enabled=False,
                    include_environment_context=False,
                    supports_image_input=media,
                    supports_audio_input=media,
                    content_item_kinds=kinds,
                    remote_compaction_v2=old_v2,
                    context_window_tokens=1000000,
                    auto_compact_tokens=50000 if automatic else 900000,
                ),
                model=OpenAIResponsesModel(
                    api_key="fixture",
                    base_url="https://fixture.invalid/v1",
                    capabilities=caps,
                    client=client,
                ),
                registry=ToolRegistry(),
                database_path=tmp_path / "s.db",
                home_path=tmp_path / ".corki",
                thread_id=thread,
            )

        runtime = create(old_v2=False)
        try:
            await runtime._ensure_ready()
            source = UserMessageItem(
                "source",
                "source-turn",
                content_items=(
                    TextContent("retained source before" * (10 if boundary else 1)),
                    ImageAttachment(PNG),
                    AudioAttachment(AUDIO),
                    TextContent("retained source after"),
                ),
            )
            # compact.rs's 20,000-token text allowance leaves eight tokens
            # for the older message, exercising prefix/suffix truncation.
            newer = (UserMessageItem("B" * (4 * 19992), "newer-turn"),) if boundary else ()
            await runtime._repository.append_items(
                runtime.thread_id,
                (
                    source,
                    *newer,
                    AssistantMessageItem("old " * 60000, "source-turn", "source-step"),
                ),
            )
            thread = runtime.thread_id
            before = await runtime._repository.load_items(thread)
            await runtime.aclose()
            runtime = create(thread)
            if not automatic:
                assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
            assert isinstance([e async for e in runtime.stream("current")][-1], TurnCompleted)
            assert len(bodies) == 2  # ordinary summary, then main response
            submitted = next(i for i in bodies[0]["input"] if i.get("id") == f"msg_{source.id}")
            assert [part["type"] for part in submitted["content"]] == [
                "input_text",
                "input_image" if supported else "input_text",
                "input_audio" if supported else "input_text",
                "input_text",
            ]
            assert "current" not in json.dumps(bodies[0]["input"])
            submitted_text = "\n".join(
                part["text"] for part in submitted["content"] if part["type"] == "input_text"
            )
            if not supported:
                assert UNSUPPORTED in submitted_text and UNSUPPORTED_INPUT in submitted_text
            # Ordinary compact.rs collects the annotated history's user text,
            # not model-request media fallback notices. The source is immutable.
            expected_text = "\n".join(
                part.text for part in source.content_items if isinstance(part, TextContent)
            )
            stored = await runtime._repository.load_items(thread)
            assert stored[: len(before)] == before
            markers = [i for i in stored if isinstance(i, CompactionItem)]
            assert len(markers) == 1 and markers[0].remote_payload_json is None
            assert markers[0].summary == "ordinary summary"
            retained = next(
                i
                for i in active_history(stored)
                if isinstance(i, UserMessageItem) and i.retained_from_id == source.id
            )
            if boundary:
                # Eight ASCII tokens keep 16 bytes per end; the marker is extra.
                omitted_tokens = (len(expected_text) - 32 + 3) // 4
                assert retained.content == (
                    expected_text[:16]
                    + f"…{omitted_tokens} tokens truncated…"
                    + expected_text[-16:]
                )
                assert (
                    next(
                        i.content
                        for i in active_history(stored)
                        if isinstance(i, UserMessageItem) and i.retained_from_id == newer[0].id
                    )
                    == newer[0].content
                )
            else:
                assert retained.content == expected_text
            assert retained.content_items == () and retained.attachments == ()
            assert retained.id != source.id and retained.created_at == source.created_at
            expected_wire = next(
                i for i in bodies[-1]["input"] if i.get("id") == f"msg_{source.id}"
            )
            assert expected_wire["content"] == [{"type": "input_text", "text": retained.content}]
            # A capability change and another summary must not resurrect media
            # or invent a new original contribution after cold recovery.
            for iteration in range(2):
                await runtime.aclose()
                runtime = create(thread, media=True, old_v2=bool(iteration))
                assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
                assert isinstance(
                    [e async for e in runtime.stream(f"cold next {iteration}")][-1], TurnCompleted
                )
                current = await runtime._repository.load_items(thread)
                assert current[: len(stored)] == stored
                copy = next(
                    i
                    for i in active_history(current)
                    if isinstance(i, UserMessageItem) and i.retained_from_id == source.id
                )
                assert copy.id != retained.id and copy.created_at == source.created_at
                assert copy.content_items == () and copy.attachments == ()
                cold_wire = next(
                    i for i in bodies[-1]["input"] if i.get("id") == expected_wire["id"]
                )
                assert cold_wire["content"] == [{"type": "input_text", "text": copy.content}]
                if not boundary:
                    assert cold_wire == expected_wire
                stored = current
            assert len(bodies) == 6
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("automatic", [False, True])
def test_ordinary_retention_budget_boundary_survives_install_and_reopen(tmp_path, automatic):
    test_ordinary_summary_preserves_source_text_after_cold_reopen(
        tmp_path, automatic, True, "openai", True, boundary=True
    )
