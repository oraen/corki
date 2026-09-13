"""Budget boundaries move content and its positional annotation together."""

import asyncio
import base64
from dataclasses import replace

import pytest

from corki.context.window import _retained_user_messages
from corki.core.checkpoint import checkpoint_serializer
from corki.models.responses import _to_response_input
from corki.protocol.items import (
    UserMessageItem,
    item_from_payload,
    item_to_payload,
)
from corki.protocol.tools import TextContent
from corki.storage import SQLiteSessionRepository

META = "internal_chat_message_metadata_passthrough"
# Serialized by the immutable144 installed wheel, not the current writer.
OLD_USER = (
    "x7MCk7Rjb3JraS5wcm90b2NvbC5pdGVtc69Vc2VyTWVzc2FnZUl0ZW2Ip2NvbnRlbnSjb2xkp3R1cm5faWSk"
    "dHVybqJpZKhvbGQtdXNlcqthdHRhY2htZW50c5CqY3JlYXRlZF9hdLkyMDI2LTA5LTA4VDAwOjAwOjAwKzAw"
    "OjAwrWNvbnRlbnRfaXRlbXOQsHJldGFpbmVkX2Zyb21faWTAsmNvbnRlbnRfaXRlbV9raW5kc8A="
)


@pytest.mark.parametrize("shape", [None, (), ("first",), ("first", "second", "excess")])
def test_local_text_reconstruction_keeps_source_id_and_reclassifies_single_text(shape):
    item = UserMessageItem(
        "", "turn", content_items=(TextContent("one"), TextContent("two")), content_item_kinds=shape
    )
    result = _retained_user_messages((item,), excluded_ids=set(), token_budget=1000)[0]
    wire = _to_response_input(result)
    assert wire["content"] == [{"type": "input_text", "text": "one\ntwo"}]
    assert META not in wire
    assert result.content_item_kinds == (None if shape is None else ("user.text",))
    assert wire["id"] == f"msg_{item.id}"
    assert result.retained_from_id == item.id


def test_legacy_checkpoint_user_retention_and_sqlite_replay(tmp_path):
    async def scenario():
        original = UserMessageItem(
            "old", "turn", id="old-user", created_at="2026-09-08T00:00:00+00:00"
        )
        serializer = checkpoint_serializer()
        restored = serializer.loads_typed(("msgpack", base64.b64decode(OLD_USER)))
        assert restored == original
        assert item_from_payload("user_message", item_to_payload(original)) == original
        retained = _retained_user_messages((restored,), excluded_ids=set(), token_budget=1000)[0]
        assert serializer.loads_typed(serializer.dumps_typed(retained)) == retained
        repository = SQLiteSessionRepository(tmp_path / "s.db")
        try:
            await repository.create_thread("thread", tmp_path)
            await repository.append_items("thread", (original, retained))
            await repository.append_items("thread", (replace(retained, created_at="later"),))
            assert await repository.load_items("thread") == (original, retained)
            assert _to_response_input(retained) == _to_response_input(original)
        finally:
            await repository.close()

    asyncio.run(scenario())
