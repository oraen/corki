"""Source-mapped audio preparation cases; codec decoding is not a prerequisite."""

import asyncio
import base64
import threading

import pytest

from corki.media import audio as audio_module
from corki.media import preparation as preparation_module
from corki.media.audio import (
    PROCESSING_ERROR,
    TOO_LARGE,
    UNSUPPORTED_FORMAT,
    UNSUPPORTED_INPUT,
    prepare_audio,
)
from corki.media.preparation import MediaPreparation
from corki.protocol.ids import new_tool_call_id, new_turn_id
from corki.protocol.items import UserMessageItem, item_from_payload, item_to_payload
from corki.protocol.tools import AudioAttachment, CodeModeOutput, TextContent, ToolResult


@pytest.mark.parametrize(
    "mime,canonical",
    [
        ("audio/wav", "audio/wav"),
        ("audio/x-wav", "audio/wav"),
        ("audio/wave", "audio/wav"),
        ("audio/vnd.wave", "audio/wav"),
        ("audio/mpeg", "audio/mpeg"),
        ("audio/mp3", "audio/mpeg"),
        ("audio/mp4", "audio/mp4"),
        ("audio/m4a", "audio/mp4"),
        ("audio/x-m4a", "audio/mp4"),
        ("audio/webm", "audio/webm"),
        ("audio/ogg", "audio/ogg"),
    ],
)
def test_mime_aliases_and_case_canonicalized_without_codec_decode(mime, canonical):
    assert prepare_audio(
        AudioAttachment(f"DaTa:{mime.upper()};ignored=1;BASE64,YXVkaW8=")
    ) == AudioAttachment(f"data:{canonical};base64,YXVkaW8=")


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://example.test/audio.mp3", PROCESSING_ERROR),
        ("file:///audio.wav", PROCESSING_ERROR),
        ("data:audio/wav;base64", PROCESSING_ERROR),
        ("data:;base64,YQ==", PROCESSING_ERROR),
        ("data:audio/wav,not-base64", PROCESSING_ERROR),
        ("data:audio/wav;base64,%%%", PROCESSING_ERROR),
        ("data:audio/wav;base64,YR==", PROCESSING_ERROR),
        ("data:audio/wav;base64,YQ===", PROCESSING_ERROR),
        ("data:audio/wav;base64,YQ", PROCESSING_ERROR),
        ("data:audio/wav;base64,秘密", PROCESSING_ERROR),
        ("data:audio/flac;base64,YQ==", UNSUPPORTED_FORMAT),
        ("data:audio/flac,invalid", UNSUPPORTED_FORMAT),
    ],
)
def test_processing_and_unsupported_errors_have_distinct_stable_placeholders(url, expected):
    assert prepare_audio(AudioAttachment(url)) == TextContent(expected)


@pytest.mark.parametrize("size", [4, 5, 7])
def test_encoded_and_decoded_size_limits_independently_enforced(monkeypatch, size):
    monkeypatch.setattr(audio_module, "MAX_AUDIO_BYTES", 4)
    part = AudioAttachment("data:audio/wav;base64," + base64.b64encode(b"a" * size).decode())
    assert prepare_audio(part) == (part if size == 4 else TextContent(TOO_LARGE))


def test_empty_base64_matches_codex_preparation_not_decodability():
    part = AudioAttachment("data:audio/wav;base64,")
    assert prepare_audio(part) == part


def test_result_metadata_typed_raw_value_and_user_payload_are_preserved():
    async def scenario():
        raw = "data:audio/x-wav;base64,YXVkaW8="
        result = ToolResult(
            new_tool_call_id(),
            "fixture",
            "diagnostic",
            is_error=True,
            display_content="display",
            code_mode_output=CodeModeOutput({"audio_url": raw}),
            content_items=(
                TextContent("before"),
                AudioAttachment(raw),
                AudioAttachment("data:audio/wav,no"),
            ),
        )
        service = MediaPreparation()
        prepared = await service.prepare_result(result)
        assert prepared.is_error and prepared.display_content == "display"
        assert prepared.code_mode_output == result.code_mode_output
        assert prepared.call_id == result.call_id and prepared.tool_name == result.tool_name
        assert prepared.content_items == (
            TextContent("before"),
            AudioAttachment("data:audio/wav;base64,YXVkaW8="),
            TextContent(PROCESSING_ERROR),
        )
        user = UserMessageItem(
            "original user words", new_turn_id(), content_items=result.content_items
        )
        history = await service.prepare_items((user,))
        model = await service.prepare_items(history, for_model=True)
        assert model[0].content == user.content and model[0].id == user.id
        assert model[0].content_items == (
            TextContent("before"),
            TextContent(UNSUPPORTED_INPUT),
            TextContent(PROCESSING_ERROR),
        )
        assert isinstance(history[0].content_items[1], AudioAttachment)
        assert item_from_payload("user_message", item_to_payload(history[0])) == history[0]

    asyncio.run(scenario())


def test_cancellation_joins_owned_audio_work(monkeypatch):
    async def scenario():
        entered, released, stopped = threading.Event(), threading.Event(), threading.Event()
        original = preparation_module.prepare_audio

        def blocked(part):
            entered.set()
            try:
                released.wait(3)
                return original(part)
            finally:
                stopped.set()

        monkeypatch.setattr(preparation_module, "prepare_audio", blocked)
        service = MediaPreparation()
        result = ToolResult(
            new_tool_call_id(),
            "fixture",
            "audio",
            content_items=(AudioAttachment("data:audio/wav;base64,YQ=="),),
        )
        task = asyncio.create_task(service.prepare_result(result))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            task.cancel()
            released.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert stopped.is_set()
        finally:
            released.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
