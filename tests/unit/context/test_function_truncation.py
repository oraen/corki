import asyncio
from dataclasses import replace

import pytest

from corki.context.function_output import project_function_items, truncate_function_content
from corki.core.checkpoint import checkpoint_serializer
from corki.protocol.ids import new_tool_call_id, new_turn_id
from corki.protocol.items import ToolResultItem, item_from_payload, item_to_payload
from corki.protocol.tools import (
    AudioAttachment,
    EncryptedContent,
    ImageAttachment,
    TextContent,
    ToolCall,
    ToolResult,
    ToolSpec,
)
from corki.protocol.truncation import TruncationPolicy
from corki.storage.sqlite import _result_from_json, _result_to_json
from corki.tools import ToolContext, ToolExecutor, ToolRegistry


@pytest.mark.parametrize(
    "override,expected",
    [
        (None, "abcd…2 tokens truncated…mnop"),
        (1, "ab…3 tokens truncated…op"),
        (0, "…4 tokens truncated…"),
    ],
)
def test_function_override_raw_codec_and_frozen_checkpoint(override, expected):
    raw = ToolResultItem(
        new_tool_call_id(),
        "read",
        "abcdefghijklmnop",
        new_turn_id(),
        fallback_token_limit_override=override,
        is_tool_search_output=False,
    )
    (visible,) = project_function_items((raw,), TruncationPolicy("tokens", 1))
    assert visible == replace(raw, content=expected, model_output_projected=True)
    assert project_function_items((visible,), TruncationPolicy("bytes", 0)) == (visible,)
    assert raw.content == "abcdefghijklmnop" and not raw.model_output_projected
    for item in (raw, visible):
        assert item_from_payload("tool_result", item_to_payload(item)) == item
        codec = checkpoint_serializer()
        assert codec.loads_typed(codec.dumps_typed(item)) == item
    result = ToolResult(
        raw.call_id,
        raw.tool_name,
        raw.content,
        fallback_token_limit_override=override,
        is_tool_search_output=False,
    )
    assert _result_from_json(_result_to_json(result)) == result


def test_default_fields_keep_legacy_immutable_payloads_compatible():
    raw = ToolResultItem(new_tool_call_id(), "read", "text", new_turn_id())
    payload = item_to_payload(raw)
    assert (
        not {
            "fallback_token_limit_override",
            "legacy_output_char_budget",
            "is_tool_search_output",
            "model_output_projected",
        }
        & payload.keys()
    )
    result = ToolResult(raw.call_id, raw.tool_name, raw.content)
    assert "is_tool_search_output" not in _result_to_json(result)
    assert _result_from_json(_result_to_json(result)) == result


@pytest.mark.parametrize("search", [True, False, None])
def test_search_classification_is_typed_and_legacy_has_explicit_fallback(search):
    raw = ToolResultItem(
        new_tool_call_id(), "tool_search", "abcdef", new_turn_id(), is_tool_search_output=search
    )
    (visible,) = project_function_items((raw,), TruncationPolicy("bytes", 0))
    assert visible == (
        replace(raw, content="…6 chars truncated…", model_output_projected=True)
        if search is False
        else raw
    )


@pytest.mark.parametrize("mode,limit", [("bytes", 8), ("tokens", 2)])
def test_function_parts_share_native_ordered_truncation_policy(mode, limit):
    image, cipher = ImageAttachment("image"), EncryptedContent("opaque")
    raw = (
        TextContent(""),
        TextContent("abcd"),
        image,
        TextContent("abcdefgh"),
        TextContent("tail"),
        AudioAttachment("data:audio/wav;base64,bogus"),
        cipher,
    )
    assert truncate_function_content(raw, TruncationPolicy(mode, limit)) == (
        TextContent("abcd"),
        image,
        TextContent("ab…4 chars truncated…gh" if mode == "bytes" else "ab…1 tokens truncated…gh"),
        cipher,
        TextContent("[omitted 1 text items ...]"),
        TextContent("[omitted 1 audio items ...]"),
    )
    assert raw[3].text == "abcdefgh"


@pytest.mark.parametrize("override", [True, -1, "1", 1.5])
def test_invalid_host_override_is_an_observation(tmp_path, override):
    class Tool:
        spec = ToolSpec("read", "read", {})

        async def execute(self, call, context):
            return ToolResult(call.id, call.name, "raw", fallback_token_limit_override=override)

    async def scenario():
        registry = ToolRegistry()
        registry.register(Tool())
        result = await ToolExecutor(registry, output_char_budget=1000).execute(
            ToolCall(new_tool_call_id(), "read", {}), ToolContext(tmp_path)
        )
        assert result.is_error and result.fallback_token_limit_override is None
        assert "fallback_token_limit_override" in result.content

    asyncio.run(scenario())


def test_raw_transport_guard_is_not_a_model_truncation_budget(tmp_path, monkeypatch):
    monkeypatch.setattr("corki.tools.executor.MAX_RAW_TOOL_RESULT_BYTES", 128)

    class Tool:
        spec = ToolSpec("read", "read", {})

        async def execute(self, call, context):
            return ToolResult(call.id, call.name, "字" * 50)

    async def scenario():
        registry = ToolRegistry()
        registry.register(Tool())
        executor = ToolExecutor(registry, output_char_budget=1000)
        call = ToolCall(new_tool_call_id(), "read", {})
        result = await executor.execute(call, ToolContext(tmp_path))
        assert result.is_error and "raw transport byte limit" in result.content
        diagnostic = executor.error(call, "字" * 1000)
        assert len(diagnostic.content.encode("utf-8")) <= 128

    asyncio.run(scenario())
