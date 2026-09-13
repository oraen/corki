"""Plan explanations survive history and completed-tool ledger replay."""

import json

import pytest

from corki.protocol.ids import new_tool_call_id, new_turn_id
from corki.protocol.items import ToolResultItem, item_from_payload, item_to_payload
from corki.protocol.tools import ToolResult, ToolStateUpdate
from corki.storage.sqlite import _result_from_json, _result_to_json


@pytest.mark.parametrize("explanation", [None, "Inspect before changing [red]files[/red]."])
def test_plan_explanation_survives_history_and_ledger(explanation):
    update = ToolStateUpdate(
        plan=({"step": "Inspect", "status": "pending"},), plan_explanation=explanation
    )
    call_id = new_tool_call_id()
    result = ToolResult(call_id, "update_plan", "Plan updated.", state_update=update)
    encoded = _result_to_json(result)
    assert _result_from_json(encoded) == result
    assert ("plan_explanation" in json.loads(encoded)) is (explanation is not None)
    item = ToolResultItem(
        call_id, "update_plan", result.content, new_turn_id(), state_update=update
    )
    payload = item_to_payload(item)
    assert ("plan_explanation" in payload["state_update"]) is (explanation is not None)
    assert item_from_payload("tool_result", payload) == item
