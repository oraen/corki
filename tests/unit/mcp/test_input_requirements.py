from dataclasses import replace
from types import SimpleNamespace

import pytest

from corki.core.checkpoint import checkpoint_serializer
from corki.mcp.input_requirements import collect_input_requirements
from corki.protocol import InputMention
from corki.protocol.ids import TurnId, new_tool_call_id
from corki.protocol.items import (
    AssistantMessageItem,
    ToolResultItem,
    UserMessageItem,
    item_from_payload,
    item_to_payload,
    new_step_id,
)
from corki.skills.mentions import linked_paths


def collect(tmp_path, state, *items, **options):
    return collect_input_requirements(
        state, items, cwd=tmp_path, skills=None, plugins=SimpleNamespace(plugins=()), **options
    )


def test_only_original_current_turn_user_inputs_accumulate(tmp_path):
    turn = TurnId("turn")
    user = UserMessageItem("[$docs](mcp://pending)", turn)
    previous = UserMessageItem("[$docs](mcp://previous)", TurnId("previous"))
    assistant = AssistantMessageItem("[$docs](mcp://assistant)", turn, new_step_id())
    output = ToolResultItem(new_tool_call_id(), "fixture", "[$docs](mcp://tool)", turn)
    retained = replace(user, content="[$docs](mcp://summary)", retained_from_id=user.id)
    state = {"turn_id": turn}
    result = collect(tmp_path, state, previous, assistant, output, retained, user)
    assert result["mcp_required_servers"] == ("pending",)
    assert result["mcp_requirement_input_ids"] == (user.id,)
    next_state = {**state, **result}
    later = UserMessageItem("[$new](mcp://later)", turn)
    result = collect(tmp_path, next_state, replace(user, content="[$x](mcp://changed)"), later)
    assert result["mcp_required_servers"] == ("later", "pending")
    assert collect(tmp_path, {**state, **result}) == result
    assert collect(tmp_path, next_state, later, disabled=True)["mcp_required_servers"] == ()


@pytest.mark.parametrize(
    "text,paths",
    [
        ("[@label](plugin://opaque@market?app=desktop)", {"plugin://opaque@market?app=desktop"}),
        ("[$label](plugin://opaque)", set()),
        ("[@HOME](plugin://opaque)", set()),
        ("@display-name", set()),
        ("[@label]\n(plugin://opaque)", {"plugin://opaque"}),
        ("[@label](\x1cplugin://opaque\x1c)", {"\x1cplugin://opaque\x1c"}),
    ],
)
def test_plugin_link_sigil_and_native_whitespace(text, paths):
    assert linked_paths(text, sigil="@") == paths


def test_structured_opaque_plugin_ids_are_not_display_names(tmp_path):
    user = UserMessageItem(
        "[@different](plugin://text-id?browserFamily=chromium)",
        TurnId("t"),
        mentions=(InputMention("display-only", "plugin://opaque@market?app=x"),),
    )
    result = collect(tmp_path, {"turn_id": user.turn_id}, user)
    assert result["mcp_required_plugins"] == ("opaque@market", "text-id")


def test_mentions_round_trip_without_changing_legacy_payload():
    plain = UserMessageItem("text", TurnId("turn"))
    assert "mentions" not in item_to_payload(plain)
    assert item_from_payload("user_message", item_to_payload(plain)) == plain
    user = replace(plain, mentions=(InputMention("label", "mcp://docs"),))
    assert item_from_payload("user_message", item_to_payload(user)) == user
    serde = checkpoint_serializer()
    assert serde.loads_typed(serde.dumps_typed(user)) == user


@pytest.mark.parametrize(
    "key", ["mcp_required_servers", "mcp_required_plugins", "mcp_requirement_input_ids"]
)
def test_invalid_checkpoint_requirements_rejected(tmp_path, key):
    with pytest.raises(ValueError, match=key):
        collect(tmp_path, {"turn_id": TurnId("t"), key: [False]})
