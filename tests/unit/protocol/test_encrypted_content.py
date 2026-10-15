import asyncio
from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.context.tokens import estimate_item_tokens
from corki.context.tool_output import truncate_content
from corki.core.checkpoint import checkpoint_serializer
from corki.models.media import UNSUPPORTED_ENCRYPTED
from corki.protocol.ids import ThreadId, TurnId, new_tool_call_id
from corki.protocol.items import (
    ToolResultItem,
    UserMessageItem,
    item_from_payload,
    item_kind,
    item_to_payload,
)
from corki.protocol.tools import (
    EncryptedContent,
    ImageAttachment,
    TextContent,
    ToolCall,
    ToolResult,
    ToolSpec,
    content_text,
)
from corki.storage import SQLiteSessionRepository
from corki.tools import ToolRegistry
from corki.tools.base import ToolContext
from corki.tools.executor import ToolExecutor


def test_ciphertext_codec_and_ledger_are_lossless_without_text_projection(tmp_path):
    async def scenario():
        cipher = EncryptedContent('opaque"\\\n界==')
        parts = (TextContent("before"), cipher, TextContent("after"))
        item = ToolResultItem(
            new_tool_call_id(), "recover", content_text(parts), TurnId("turn"), content_items=parts
        )
        assert item_from_payload(item_kind(item), item_to_payload(item)) == item
        serializer = checkpoint_serializer()
        assert serializer.loads_typed(serializer.dumps_typed(item)) == item
        assert cipher.encrypted_content not in repr(item)
        assert cipher.encrypted_content not in content_text(parts)
        repository = SQLiteSessionRepository(tmp_path / "sessions.db")
        thread = ThreadId("thread")
        await repository.create_thread(thread, tmp_path)
        await repository.append_items(thread, (item,))
        call = ToolCall(item.call_id, "recover", {})
        assert await repository.claim_tool_call(thread, item.turn_id, call) is None
        result = ToolResult(call.id, call.name, content_text(parts), content_items=parts)
        await repository.complete_tool_call(thread, item.turn_id, result)
        reopened = SQLiteSessionRepository(tmp_path / "sessions.db")
        assert await reopened.load_items(thread) == (item,)
        assert await reopened.claim_tool_call(thread, item.turn_id, call) == result
        assert cipher.encrypted_content not in str(await reopened.load_messages(thread))

    asyncio.run(scenario())


@pytest.mark.parametrize("length", [0, 1, 16, 1868, 8000])
def test_opaque_cost_matches_unavailable_notice_not_archived_ciphertext(length):
    part = EncryptedContent("界" * length)
    item = ToolResultItem(
        new_tool_call_id(), "recover", "ignored", TurnId("turn"), content_items=(part,)
    )
    projected = replace(item, content_items=(TextContent(UNSUPPORTED_ENCRYPTED),))
    assert estimate_item_tokens(item) == estimate_item_tokens(projected)
    custom = replace(item, input_kind="freeform")
    assert estimate_item_tokens(custom) == estimate_item_tokens(projected)


@pytest.mark.parametrize("formatted", [False, True])
def test_text_budget_never_consumes_or_slices_opaque_blocks(formatted):
    cipher = EncryptedContent("opaque==" * 10000)
    image = ImageAttachment("data:image/png;base64,fixture")
    result = truncate_content(
        (TextContent("a"), cipher, image, TextContent("b")), 0, formatted_text=formatted
    )
    assert result[:2] == (cipher, image)
    assert result[-1] == TextContent("[omitted 2 text items ...]")


@pytest.mark.parametrize("bad", [None, 1, b"bytes"])
def test_invalid_cipher_type_is_rejected(bad):
    with pytest.raises(ValueError, match="string"):
        EncryptedContent(bad)


def test_cipher_is_not_a_user_input_content_variant():
    with pytest.raises(ValueError, match="tool results"):
        UserMessageItem("", TurnId("turn"), content_items=(EncryptedContent("opaque"),))


def test_oversize_cipher_is_observation_error_not_truncated_success(tmp_path):
    async def scenario():
        class Oversize:
            spec = ToolSpec("large", "read", {"type": "object"})

            async def execute(self, call, context):
                return ToolResult(
                    call.id, call.name, "safe", content_items=(EncryptedContent("Z" * 32_000_001),)
                )

        registry = ToolRegistry()
        registry.register(Oversize())
        result = await ToolExecutor(registry, output_char_budget=1024).execute(
            ToolCall(new_tool_call_id(), "large", {}), ToolContext(tmp_path)
        )
        assert result.is_error and not result.content_items
        assert "byte limit" in result.content and "ZZZZZZ" not in result.content

    asyncio.run(scenario())


def test_legacy_native_capability_cannot_reenable_excluded_protocol(tmp_path):
    config = tmp_path / "corki.toml"
    config.write_text('[provider]\napi_mode = "responses"\nsupports_encrypted_tool_output = true\n')
    with pytest.raises(ValueError, match="unsupported"):
        CorkiSettings.for_directory(tmp_path, config_file=config)
    with pytest.raises(ValueError, match="unsupported"):
        CorkiSettings(working_directory=tmp_path, supports_encrypted_tool_output=True)


@pytest.mark.parametrize("invalid", [None, "true", 1])
def test_native_capability_requires_explicit_boolean(tmp_path, invalid):
    with pytest.raises(ValueError, match="supports_encrypted_tool_output"):
        CorkiSettings(working_directory=tmp_path, supports_encrypted_tool_output=invalid)
