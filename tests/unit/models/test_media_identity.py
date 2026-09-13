import asyncio
import json

import pytest

from corki.media.audio import UNSUPPORTED_INPUT
from corki.media.preparation import MediaPreparation
from corki.models.responses import _to_response_input
from corki.protocol.items import AssistantMessageItem


@pytest.mark.parametrize("metadata", [None, '{"id":"msg_old"}'])
@pytest.mark.parametrize("supports_audio", [False, True])
def test_assistant_audio_projection_preserves_identity_without_private_fields(
    metadata, supports_audio
):
    async def scenario():
        body = {
            "content": [
                {"type": "output_text", "text": UNSUPPORTED_INPUT},
                {"type": "input_audio", "audio_url": "data:audio/mp4;base64,YXVkaW8="},
                {"type": "output_text", "text": "tail"},
            ],
        }
        source = AssistantMessageItem(
            "",
            "turn",
            "step",
            response_body_json=json.dumps(body),
            response_item_metadata_json=metadata,
        )
        service = MediaPreparation(supports_audio=supports_audio)
        (prepared,) = await service.prepare_items((source,), for_model=True)
        assert prepared.response_item_metadata_json == metadata
        expected = list(body["content"])
        if not supports_audio:
            expected[1] = {"type": "input_text", "text": UNSUPPORTED_INPUT}
        assert json.loads(prepared.response_body_json) == {"content": expected}
        assert source.response_body_json == json.dumps(body)
        assert await service.prepare_items((prepared,), for_model=True) == (prepared,)
        wire = _to_response_input(prepared, audio_enabled=supports_audio)
        assert wire["content"] == expected
        assert "internal_chat_message_metadata_passthrough" not in wire
        assert wire["id"] == ("msg_old" if metadata else f"msg_{source.id}")

    asyncio.run(scenario())
