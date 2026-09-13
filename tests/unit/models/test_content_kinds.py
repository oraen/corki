"""Typed classification projection and pre-field storage/checkpoint compatibility."""

import asyncio
import base64
from copy import deepcopy

import pytest

from corki.context.tokens import estimate_item_tokens
from corki.core.checkpoint import checkpoint_serializer
from corki.media.images import ImagePolicy
from corki.media.preparation import MediaPreparation
from corki.models.responses import _to_response_input
from corki.protocol.content_kinds import project_message_media
from corki.protocol.items import (
    AssistantMessageItem,
    UserMessageItem,
    item_from_payload,
    item_to_payload,
)
from corki.protocol.tools import AudioAttachment, TextContent
from corki.storage import SQLiteSessionRepository

META = "internal_chat_message_metadata_passthrough"
# Produced by the immutable140 installed package, not by the new constructor.
OLD_USER = (
    "x58Ck7Rjb3JraS5wcm90b2NvbC5pdGVtc69Vc2VyTWVzc2FnZUl0ZW2Hp2NvbnRlbnSmc291cmNl"
    "p3R1cm5faWSkdHVybqJpZKVsb2NhbKthdHRhY2htZW50c5CqY3JlYXRlZF9hdLkyMDI2LTA5LTA4"
    "VDAwOjAwOjAwKzAwOjAwrWNvbnRlbnRfaXRlbXOQsHJldGFpbmVkX2Zyb21faWTA"
)


@pytest.mark.parametrize("kinds", [None, [], ["original"], ["original", "media", "excess"]])
def test_message_projection_pairs_and_preserves_source(kinds):
    source = {
        "type": "message",
        "role": "assistant",
        "content": [
            {"type": "output_text", "text": "unchanged"},
            {"type": "input_audio", "audio_url": "data:audio/mp4;base64,YQ=="},
        ],
        META: {"turn_id": "source", "content_item_kinds": kinds},
    }
    before = deepcopy(source)
    assert project_message_media(source, {}) is source
    result = project_message_media(source, {"input_audio": ("audio.unsupported", "notice")})
    assert result == {
        **source,
        "content": [source["content"][0], {"type": "input_text", "text": "notice"}],
        META: {
            "turn_id": "source",
            "content_item_kinds": [kinds[0] if kinds else "unknown", "audio.unsupported"],
        },
    }
    assert source == before
    assert project_message_media(result, {"input_audio": ("audio.unsupported", "notice")}) == result


def test_ordinary_adapter_audio_replacement_is_not_text_inference():
    source = {
        "type": "message",
        "role": "user",
        "content": [
            {
                "type": "input_text",
                "text": "audio content omitted because you do not support audio input",
            },
            {"type": "input_audio", "audio_url": "data:audio/mp4;base64,YQ=="},
        ],
        META: {"content_item_kinds": ["user.text", "user.audio"]},
    }
    item = UserMessageItem(
        "",
        "turn",
        content_items=(
            TextContent(source["content"][0]["text"]),
            AudioAttachment(source["content"][1]["audio_url"]),
        ),
        content_item_kinds=source[META]["content_item_kinds"],
    )
    projected = _to_response_input(item)
    assert META not in projected
    assert projected["content"][0] == source["content"][0]
    assert projected["content"][1]["type"] == "input_text"
    assert "audio" in projected["content"][1]["text"]
    assert _to_response_input(item, audio_enabled=True)["content"] == source["content"]
    assert item.content_items[0].text == source["content"][0]["text"]
    assert item.content_items[1].data_url == source["content"][1]["audio_url"]


@pytest.mark.parametrize("kinds", [[], ["user.text"], ["host.notice", "extra"]])
def test_user_classifications_round_trip_without_wire_budget(kinds):
    item = UserMessageItem("source", "turn", content_item_kinds=kinds)
    assert item.content_item_kinds == tuple(kinds)
    restored = item_from_payload("user_message", item_to_payload(item))
    assert restored == item
    serializer = checkpoint_serializer()
    assert serializer.loads_typed(serializer.dumps_typed(restored)) == item
    large = UserMessageItem("source", "turn", content_item_kinds=["x" * 10000])
    assert estimate_item_tokens(large) == estimate_item_tokens(item)


@pytest.mark.parametrize("bad", ["user.text", [False], [None], {"kind": "user.text"}])
def test_invalid_user_classifications_are_not_silently_coerced(bad):
    with pytest.raises(ValueError, match="kinds must be strings"):
        UserMessageItem("source", "turn", content_item_kinds=bad)


def test_old_user_checkpoint_and_sqlite_payload_keep_absent_field(tmp_path):
    async def scenario():
        item = checkpoint_serializer().loads_typed(("msgpack", base64.b64decode(OLD_USER)))
        assert item == UserMessageItem(
            "source", "turn", id="local", created_at="2026-09-08T00:00:00+00:00"
        )
        assert item.content_item_kinds is None
        payload = item_to_payload(item)
        assert "content_item_kinds" not in payload
        restored = item_from_payload("user_message", payload)
        repository = SQLiteSessionRepository(tmp_path / "s.db")
        try:
            await repository.create_thread("thread", tmp_path)
            await repository.append_items("thread", (restored,))
            await repository.append_items("thread", (item,))
            assert await repository.load_items("thread") == (item,)
        finally:
            await repository.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("images,audio", [(False, True), (True, False), (True, True)])
def test_modality_gate_normalizes_legacy_vectors_even_without_media(images, audio):
    async def scenario():
        user = UserMessageItem("source", "turn", content_item_kinds=["user.text", "extra"])
        assistant = AssistantMessageItem("source", "turn", "step")
        prepared = await MediaPreparation(
            ImagePolicy(supports_images=images), supports_audio=audio
        ).prepare_items((user, assistant), for_model=True)
        if images and audio:
            assert prepared == (user, assistant)
        else:
            assert prepared[0].content_item_kinds == ("user.text",)
            assert prepared[1] == assistant
        assert assistant.response_item_metadata_json is None
        assert user.content_item_kinds == ("user.text", "extra")

    asyncio.run(scenario())
