"""Ordered content durability, costs, and normalization boundaries."""

import asyncio
import base64
import io
import json
import sqlite3
import struct
import wave
from dataclasses import dataclass, replace

import pytest

from corki.config import CorkiSettings
from corki.context.tokens import estimate_audio_tokens, estimate_item_tokens
from corki.context.tool_output import truncate_content
from corki.core.checkpoint import checkpoint_serializer
from corki.models.media import chat_content
from corki.protocol.audio import wav_duration_seconds
from corki.protocol.ids import new_thread_id, new_tool_call_id, new_turn_id
from corki.protocol.items import ToolResultItem, UserMessageItem, item_to_payload
from corki.protocol.memory import MemoryCitation, MemoryCitationEntry
from corki.protocol.tools import (
    AudioAttachment,
    ImageAttachment,
    TextContent,
    ToolCall,
    ToolResult,
    ToolSpec,
    ToolStateUpdate,
)
from corki.storage import SQLiteSessionRepository
from corki.storage.sqlite import StorageIntegrityError
from corki.tools import ToolContext, ToolExecutor, ToolRegistry


def wav_url(milliseconds=1000, *, streaming=False):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(1000)
        wav.writeframes(b"\0\0" * milliseconds)
    raw = stream.getvalue()
    if streaming:
        raw = raw[:4] + b"\xff" * 4 + raw[8:40] + b"\xff" * 4 + raw[44:]
    return "data:audio/wav;base64," + base64.b64encode(raw).decode()


@pytest.mark.parametrize("milliseconds", [0, 24, 25, 1000])
@pytest.mark.parametrize("streaming", [False, True])
def test_actual_wav_data_determines_duration(milliseconds, streaming):
    url = wav_url(milliseconds, streaming=streaming)
    assert wav_duration_seconds(url) == milliseconds / 1000
    assert estimate_audio_tokens(AudioAttachment(url)) == (milliseconds + 99) // 100


def test_unknown_audio_uses_nonzero_fallback_and_images_survive_output_budget():
    audio = AudioAttachment("data:audio/unknown;base64,YWJj")
    assert wav_duration_seconds(audio.data_url) is None
    assert estimate_audio_tokens(audio) > 0
    image = ImageAttachment("data:image/png;base64,YWJj")
    parts = truncate_content((TextContent("before"), image, audio, TextContent("after")), 0)
    assert parts == (
        image,
        TextContent("[omitted 2 text items ...]"),
        TextContent("[omitted 1 audio items ...]"),
    )


def test_text_and_audio_share_budget_in_original_order():
    audio = AudioAttachment(wav_url())  # ten tokens
    assert truncate_content((TextContent("abcd"), audio, TextContent("tail")), 11) == (
        TextContent("abcd"),
        audio,
        TextContent("[omitted 1 text items ...]"),
    )
    assert truncate_content((TextContent("abcd"), audio, TextContent("tail")), 10) == (
        TextContent("abcd"),
        TextContent("tail"),
        TextContent("[omitted 1 audio items ...]"),
    )


def test_all_text_code_mode_truncation_reports_original_size():
    parts = (TextContent("head" * 100), TextContent("tail" * 100))
    result = truncate_content(parts, 20, formatted_text=True)
    assert len(result) == 1 and "Warning: truncated output" in result[0].text
    assert "Total output lines: 2" in result[0].text
    assert "head" in result[0].text and "tail" in result[0].text


@pytest.mark.parametrize("value", ["true", 1, None])
def test_audio_capability_requires_explicit_boolean(tmp_path, value):
    with pytest.raises(ValueError, match="supports_audio_input"):
        CorkiSettings(working_directory=tmp_path, supports_audio_input=value)


def test_audio_capability_loads_from_provider_toml(tmp_path):
    path = tmp_path / "corki.toml"
    path.write_text("[provider]\nsupports_audio_input = true\n", encoding="utf-8")
    assert CorkiSettings.for_directory(tmp_path, config_file=path).supports_audio_input is True


def test_ordered_content_round_trips_through_checkpoint_serializer():
    item = ToolResultItem(
        new_tool_call_id(),
        "exec",
        "diagnostic",
        new_turn_id(),
        content_items=(
            TextContent("before"),
            AudioAttachment(wav_url()),
            ImageAttachment("data:image/png;base64,YWJj"),
            TextContent("after"),
        ),
    )
    serializer = checkpoint_serializer()
    assert serializer.loads_typed(serializer.dumps_typed(item)) == item


@dataclass
class NonProtocolCheckpointValue:
    text: str


def test_checkpoint_allowlist_does_not_construct_arbitrary_extension_types(caplog):
    serializer = checkpoint_serializer()
    encoded = serializer.dumps_typed(NonProtocolCheckpointValue("fixture"))
    assert serializer.loads_typed(encoded) == {"text": "fixture"}
    assert "Blocked deserialization" in caplog.text


def test_old_protocol_collection_fields_remain_canonical_after_checkpoint():
    serializer = checkpoint_serializer()
    values = [
        UserMessageItem("input", new_turn_id(), attachments=(ImageAttachment("data:a"),)),
        ToolStateUpdate(plan=({"step": "verify", "status": "completed"},)),
        MemoryCitation((MemoryCitationEntry("memory.md", 1, 2, "used"),), (new_thread_id(),)),
    ]
    assert serializer.loads_typed(serializer.dumps_typed(values)) == values


@pytest.mark.parametrize("mime,format", [("audio/mpeg", "mp3"), ("audio/ogg", None)])
def test_chat_audio_has_format_specific_conversion_or_explicit_omission(mime, format):
    parts = chat_content((AudioAttachment(f"data:{mime};base64,YWJj"),), audio_enabled=True)
    if format:
        assert parts == [{"type": "input_audio", "input_audio": {"data": "YWJj", "format": format}}]
    else:
        assert "requires base64 WAV or MP3" in parts[0]["text"]


@pytest.mark.parametrize("encoding", [1, 3])
def test_extensible_wav_and_odd_metadata_padding(encoding):
    fmt = struct.pack("<HHIIHHH", 0xFFFE, 1, 1000, 2000, 2, 16, 22)
    fmt += struct.pack("<HI", 16, 0) + struct.pack("<H", encoding)
    fmt += bytes.fromhex("000000001000800000aa00389b71")
    chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    chunks += b"JUNK\x01\0\0\0x\0data" + struct.pack("<I", 50) + b"\0" * 50
    raw = b"RIFF" + struct.pack("<I", len(chunks) + 4) + b"WAVE" + chunks
    url = "data:audio/wav;base64," + base64.b64encode(raw).decode()
    assert wav_duration_seconds(url) == 0.025


def test_context_counts_media_without_diagnostic_double_counting():
    part = ToolResultItem(
        new_tool_call_id(),
        "exec",
        "DIAGNOSTIC" * 10000,
        new_turn_id(),
        content_items=(TextContent("a"), AudioAttachment(wav_url())),
    )
    assert estimate_item_tokens(part) == estimate_item_tokens(replace(part, content=""))
    image = replace(
        part, content_items=(*part.content_items, ImageAttachment("data:image/png;base64,YWJj"))
    )
    assert estimate_item_tokens(image) - estimate_item_tokens(part) == 1844


def test_ordered_history_and_ledger_reopen_without_mutating_old_payloads(tmp_path):
    async def scenario():
        db = SQLiteSessionRepository(tmp_path / "history.db")
        thread, turn = new_thread_id(), new_turn_id()
        await db.create_thread(thread, tmp_path)
        parts = (
            TextContent("before"),
            ImageAttachment("data:image/png;base64,YWJj", "original"),
            AudioAttachment(wav_url()),
            TextContent("after"),
        )
        call = ToolCall(
            new_tool_call_id(), "exec", None, raw_arguments="image(...)", input_kind="freeform"
        )
        result = ToolResult(call.id, call.name, "diagnostic", content_items=parts)
        history = ToolResultItem(call.id, call.name, "diagnostic", turn, content_items=parts)
        await db.append_items(thread, (history,))
        await db.claim_tool_call(thread, turn, call)
        await db.complete_tool_call(thread, turn, result)
        reopened = SQLiteSessionRepository(db.path)
        loaded = (await reopened.load_items(thread))[0]
        assert loaded == history
        assert await reopened.claim_tool_call(thread, turn, call) == result
        await reopened.append_items(thread, (loaded,))
        with pytest.raises(StorageIntegrityError):
            await reopened.complete_tool_call(
                thread, turn, replace(result, content_items=parts[::-1])
            )
        old = replace(history, content_items=())
        assert "content_items" not in item_to_payload(old)
        with sqlite3.connect(db.path) as conn:
            payload = json.loads(
                conn.execute("SELECT result_json FROM tool_executions").fetchone()[0]
            )
        assert [item["type"] for item in payload["content_items"]] == [
            "text",
            "image",
            "audio",
            "text",
        ]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "parts",
    [
        (TextContent(42),),
        (AudioAttachment("https://remote.test/a"),),
        (ImageAttachment("data:image/png;base64,YWJj", "invalid"),),
        (object(),),
        (AudioAttachment("data:audio/wav;base64,\ud800"),),
    ],
)
def test_invalid_ordered_result_is_an_observation(tmp_path, parts):
    class Tool:
        spec = ToolSpec("fixture", "fixture", {"type": "object"})

        async def execute(self, call, context):
            return ToolResult(call.id, call.name, "diagnostic", content_items=parts)

    async def scenario():
        registry = ToolRegistry()
        registry.register(Tool())
        result = await ToolExecutor(registry, output_char_budget=1000).execute(
            ToolCall(new_tool_call_id(), "fixture", {}), ToolContext(tmp_path)
        )
        assert result.is_error and not result.content_items

    asyncio.run(scenario())
