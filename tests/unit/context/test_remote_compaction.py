import json

import pytest

from corki.context.tokens import estimate_item_tokens
from corki.models import resolve_capabilities
from corki.protocol.compaction import compaction_payload
from corki.protocol.items import (
    CompactionItem,
    item_from_payload,
    item_to_payload,
)


@pytest.mark.parametrize(
    "provider,url,mode,enabled",
    [
        ("openai", "https://fixture.invalid", "responses", False),
        (None, "https://api.openai.com/v1", "responses", False),
        ("azure", "https://fixture.invalid", "responses", False),
        ("custom", "https://sample.openai.azure.com/openai/v1", "responses", False),
        ("custom", "https://fixture.invalid", "responses", False),
        ("openai", "https://api.openai.com/v1", "chat_completions", False),
    ],
)
def test_provider_routes_compaction(provider, url, mode, enabled):
    assert (
        resolve_capabilities(
            base_url=url, api_mode=mode, provider_name=provider
        ).supports_remote_compaction
        is enabled
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"type": "compaction"},
        {"type": "compaction", "encrypted_content": None},
        {"type": "compaction", "encrypted_content": "x", "id": 1},
    ],
)
def test_bad_opaque_checkpoint_is_rejected(payload):
    with pytest.raises(ValueError):
        CompactionItem("", None, "turn", remote_payload_json=json.dumps(payload))


def test_opaque_checkpoint_payload_roundtrip_and_budget():
    payload = {"type": "compaction_summary", "id": "cmp", "encrypted_content": "x" * 2000}
    item = CompactionItem("", "before", "turn", remote_payload_json=json.dumps(payload))
    assert item_from_payload("compaction", item_to_payload(item)) == item
    assert compaction_payload(item.remote_payload_json)["type"] == "compaction"
    assert estimate_item_tokens(item) == 213
    legacy = item_to_payload(CompactionItem("summary", None, "turn"))
    assert "remote_payload_json" not in legacy
