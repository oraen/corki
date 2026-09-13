import asyncio
import json

import httpx
import pytest

from corki.config import CorkiSettings
from corki.context.history import active_history
from corki.context.tokens import estimate_item_tokens
from corki.media.images import ImagePolicy
from corki.media.preparation import MediaPreparation
from corki.models import resolve_capabilities
from corki.models.base import ModelError
from corki.models.responses import OpenAIResponsesModel, _to_response_input
from corki.models.types import ModelRequest
from corki.protocol.items import (
    CompactionItem,
    RemoteHistoryItem,
    item_from_payload,
    item_kind,
    item_to_payload,
)


def remote(payload):
    return RemoteHistoryItem(json.dumps(payload), "compact-turn")


def user(text):
    return remote(
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}
    )


@pytest.mark.parametrize("cipher", [None, "", "x" * 2000])
def test_context_compaction_is_optional_opaque_content_not_a_window_boundary(cipher):
    payload = {"type": "context_compaction", "encrypted_content": cipher}
    item = remote(payload)
    first = user("first")
    marker = CompactionItem("", None, "compact-turn", context_reset=True, replacement_item_count=2)
    assert active_history((marker, first, item)) == (first, item)
    assert item_from_payload(item_kind(item), item_to_payload(item)) == item
    with pytest.raises(ModelError, match="dedicated compaction history"):
        _to_response_input(item)
    assert estimate_item_tokens(item) == (213 if cipher else 0)


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "context_compaction", "encrypted_content": 2},
        {"type": "compaction"},
        {"type": "message", "role": "developer", "content": []},
        {"type": "message", "role": "user", "content": "text"},
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": None}]},
        {"type": "function_call", "name": "exec", "arguments": "{}"},
    ],
)
def test_normalized_history_rejects_invalid_or_executable_payloads(payload):
    with pytest.raises(ValueError):
        remote(payload)


@pytest.mark.parametrize("value", ["false", "true", '"false"'])
def test_retired_v2_config_cannot_enable_remote_protocol(tmp_path, value):
    config = tmp_path / "config.toml"
    config.write_text(f"[features]\nremote_compaction_v2={value}\n")
    assert CorkiSettings.for_directory(tmp_path, config_file=config).remote_compaction_v2 is False


def test_empty_legacy_input_is_rejected_before_http_and_auth_setup():
    async def scenario():
        def never(request):
            raise AssertionError("empty compaction made an HTTP request")

        async with httpx.AsyncClient(transport=httpx.MockTransport(never)) as client:
            model = OpenAIResponsesModel(
                api_key=None,
                base_url="https://fixture.invalid",
                capabilities=resolve_capabilities(
                    base_url="https://fixture.invalid", api_mode="responses", provider_name="openai"
                ),
                client=client,
            )
            with pytest.raises(ModelError, match="dedicated compaction"):
                _ = [
                    event
                    async for event in model.stream(
                        ModelRequest(
                            "model",
                            "instructions",
                            (),
                            (),
                            (),
                            compaction_turn_id="turn",
                            compaction_mode="legacy",
                        )
                    )
                ]

    asyncio.run(scenario())


def test_normalized_media_cost_and_model_capability_projection_preserve_archive():
    async def scenario():
        item = remote(
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "photo"},
                    {
                        "type": "input_image",
                        "image_url": "https://fixture.invalid/photo",
                        "detail": "high",
                    },
                    {"type": "input_audio", "audio_url": "data:audio/wav;base64,aGVsbG8="},
                ],
            }
        )
        assert estimate_item_tokens(item) > 1000
        media = MediaPreparation(ImagePolicy(supports_images=False), supports_audio=False)
        projected = await media.prepare_items((item,), for_model=True)
        assert all(p["type"] == "input_text" for p in projected[0].payload["content"])
        assert item.payload["content"][1]["type"] == "input_image"
        assert item.payload["content"][2]["type"] == "input_audio"
        assert not any(p["type"] == "input_audio" for p in _to_response_input(item)["content"])
        assert _to_response_input(item, audio_enabled=True) == item.payload

    asyncio.run(scenario())
