"""Legacy raw protocol settings cannot reactivate custom wire tools."""

import pytest

from corki.config import CorkiSettings
from corki.context.tokens import estimate_item_tokens, estimate_text_tokens
from corki.protocol.ids import new_tool_call_id, new_turn_id
from corki.protocol.items import ToolCallItem, new_step_id
from corki.protocol.tools import ToolCall, ToolSpec


@pytest.mark.parametrize("mode", [None, [], "disabled", "auto"])
def test_invalid_freeform_configuration_is_rejected(tmp_path, mode):
    with pytest.raises(ValueError, match="freeform_mode"):
        CorkiSettings(working_directory=tmp_path, tool_freeform_mode=mode)


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
def test_native_raw_setting_uses_ordinary_functions(tmp_path, mode):
    settings = CorkiSettings(
        working_directory=tmp_path,
        api_mode=mode,
        tool_search_mode="disabled",
        tool_freeform_mode="native",
    )
    assert settings.tool_freeform_mode == "compatible"


def test_compatible_source_escaping_is_counted_in_context():
    source = "\n" * 400
    call = ToolCall(new_tool_call_id(), "raw", None, raw_arguments=source, input_kind="freeform")
    item = ToolCallItem(call, new_turn_id(), new_step_id())
    assert estimate_item_tokens(item) >= 2 * estimate_text_tokens(source)


def test_grammar_snapshot_cannot_be_mutated_through_caller_aliases():
    grammar = {"type": "grammar", "syntax": "lark", "definition": "start: /.+/"}
    spec = ToolSpec("raw", "fixture", {}, input_kind="freeform", freeform_format=grammar)
    grammar["definition"] = "changed"
    payload = spec.as_response_tool(native_freeform=True)
    assert payload["type"] == "function" and "format" not in payload
    payload["parameters"]["properties"]["input"]["type"] = "integer"
    assert spec.compatible_parameters()["properties"]["input"]["type"] == "string"
    assert spec.freeform_format["definition"] == "start: /.+/"
