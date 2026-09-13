"""Typed body contract, checkpoint persistence, and model-visible cost."""

import json

import pytest

from corki.context.tokens import estimate_item_tokens
from corki.core.checkpoint import checkpoint_serializer
from corki.protocol.items import (
    AssistantMessageItem,
    ReasoningItem,
    item_from_payload,
    item_kind,
    item_to_payload,
)
from corki.protocol.response_body import capture_response_body, response_body_payload


@pytest.mark.parametrize("kind", ["message", "reasoning"])
def test_body_is_a_typed_projection_not_an_opaque_provider_extension(kind):
    source = {
        "type": kind,
        "content": [
            {
                "type": "output_text" if kind == "message" else "reasoning_text",
                "text": "first",
                "unknown": "discard",
            },
            {"type": "output_text" if kind == "message" else "text", "text": "second"},
        ],
        "summary": [{"type": "summary_text", "text": "summary"}],
        "unknown": {"instructions": "not-body-data"},
    }
    body = capture_response_body(source)
    decoded = response_body_payload(body, kind)
    assert "unknown" not in body
    assert [part["text"] for part in decoded["content"]] == ["first", "second"]
    item = (
        AssistantMessageItem("visible", "turn", "step", response_body_json=body)
        if kind == "message"
        else ReasoningItem("summary", "turn", "step", response_body_json=body)
    )
    assert item_from_payload(item_kind(item), item_to_payload(item)) == item
    serializer = checkpoint_serializer()
    assert serializer.loads_typed(serializer.dumps_typed(item)) == item
    assert item.content != "firstsecond"


@pytest.mark.parametrize("kind", ["message", "reasoning"])
@pytest.mark.parametrize("body", [[], "[]", "{}", '{"content":3,"summary":3}'])
def test_invalid_body_is_rejected_before_storage(kind, body):
    cls = AssistantMessageItem if kind == "message" else ReasoningItem
    with pytest.raises(ValueError):
        cls("visible", "turn", "step", response_body_json=body)


@pytest.mark.parametrize("kind", ["message", "reasoning"])
def test_hidden_text_and_many_empty_parts_are_not_free(kind):
    source = {
        "type": kind,
        "content": [
            {"type": "input_text" if kind == "message" else "reasoning_text", "text": "x" * 4000}
        ],
        "summary": [],
    }
    cls = AssistantMessageItem if kind == "message" else ReasoningItem
    item = cls("", "turn", "step", response_body_json=capture_response_body(source))
    assert estimate_item_tokens(item) >= 1000
    field = "content" if kind == "message" else "summary"
    source[field] = [
        {"type": "output_text" if kind == "message" else "summary_text", "text": ""}
    ] * 1000
    item = cls("", "turn", "step", response_body_json=capture_response_body(source))
    assert estimate_item_tokens(item) >= 1000


def test_legacy_text_only_reasoning_content_obeys_codex_serialization():
    body = capture_response_body(
        {"type": "reasoning", "summary": [], "content": [{"type": "text", "text": "legacy"}]}
    )
    assert json.loads(body) == {"summary": []}
