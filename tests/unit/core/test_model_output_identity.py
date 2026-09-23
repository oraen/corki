"""Malformed provider identities fail at the model protocol boundary."""

import pytest

from corki.core.graph import _validate_model_items
from corki.models import ModelCompleted, ModelError, ModelErrorKind
from corki.protocol.items import AssistantMessageItem, ToolCallItem
from corki.protocol.tools import ToolCall


@pytest.mark.parametrize("invalid_id", [[], 1, "\ud800"])
def test_invalid_model_item_id_is_protocol_error(invalid_id):
    item = AssistantMessageItem("answer", "turn", "step", id=invalid_id)

    with pytest.raises(ModelError) as error:
        _validate_model_items(ModelCompleted((item,)), "turn")

    assert error.value.kind == ModelErrorKind.PROTOCOL


@pytest.mark.parametrize("invalid_id", [[], 1, "\ud800"])
def test_invalid_tool_call_id_is_protocol_error(invalid_id):
    call = ToolCall(id=invalid_id, name="read_file", arguments={})
    item = ToolCallItem(call, "turn", "step")

    with pytest.raises(ModelError) as error:
        _validate_model_items(ModelCompleted((item,)), "turn")

    assert error.value.kind == ModelErrorKind.PROTOCOL


@pytest.mark.parametrize("invalid_id", [[], 1, "\ud800"])
def test_invalid_model_step_id_is_protocol_error(invalid_id):
    item = AssistantMessageItem("answer", "turn", invalid_id)

    with pytest.raises(ModelError) as error:
        _validate_model_items(ModelCompleted((item,)), "turn")

    assert error.value.kind == ModelErrorKind.PROTOCOL


@pytest.mark.parametrize("invalid_name", [1, "\ud800"])
def test_invalid_tool_name_is_protocol_error(invalid_name):
    call = ToolCall(id="call", name=invalid_name, arguments={})
    item = ToolCallItem(call, "turn", "step")

    with pytest.raises(ModelError) as error:
        _validate_model_items(ModelCompleted((item,)), "turn")

    assert error.value.kind == ModelErrorKind.PROTOCOL
