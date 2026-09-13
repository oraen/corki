import json
from dataclasses import replace

import pytest

from corki.context.tokens import estimate_item_tokens, estimate_request_tokens
from corki.models.responses import _to_response_input
from corki.protocol.items import (
    AssistantMessageItem,
    ReasoningItem,
    ToolResultItem,
    UserMessageItem,
)
from corki.protocol.tools import ToolSpec


@pytest.mark.parametrize("kind", [AssistantMessageItem, ReasoningItem])
def test_private_archive_metadata_does_not_cost_tokens(kind):
    original = kind("body", "turn", "step", response_item_metadata_json='{"id":"msg_old"}')
    decorated = replace(
        original,
        response_item_metadata_json=json.dumps(
            {
                "id": "msg_old",
                "internal_chat_message_metadata_passthrough": {"turn_id": "x" * 100_000},
            }
        ),
    )
    assert _to_response_input(original) == _to_response_input(decorated)
    assert estimate_item_tokens(original) == estimate_item_tokens(decorated)
    assert estimate_item_tokens(replace(original, content="x" * 100_000)) > 20_000


def test_internal_user_classification_is_not_request_content():
    original = UserMessageItem("body", "turn")
    decorated = replace(original, content_item_kinds=("x" * 100_000,))
    assert _to_response_input(original) == _to_response_input(decorated)
    assert estimate_item_tokens(original) == estimate_item_tokens(decorated)


def test_discovered_schemas_are_charged_in_tools_not_again_in_observation():
    spec = ToolSpec("large", "x" * 100_000, {"type": "object"})
    original = ToolResultItem("call", "tool_search", "found large", False, "turn")
    discovered = replace(original, discovered_tools=(spec,))
    assert _to_response_input(original) == _to_response_input(discovered)
    assert estimate_item_tokens(original) == estimate_item_tokens(discovered)
    assert estimate_request_tokens("", (discovered,), (spec,)) > 20_000
