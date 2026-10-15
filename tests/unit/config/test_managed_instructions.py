import json

import pytest

from corki.config.managed_instructions import REPLACEMENT, ManagedDeveloperInstructions, render
from corki.config.managed_mcp import MCPRequirementsLayer, compose_mcp_requirements


def layer(source, text):
    return MCPRequirementsLayer(source, "additional_developer_instructions = " + json.dumps(text))


@pytest.mark.parametrize("text", ["HIGH POLICY", "", " \n "])
def test_last_host_scalar_wins_including_empty_and_preserves_source(text):
    result = compose_mcp_requirements((layer("low", "x" * 50_000), layer("high", text)))
    assert result.developer_instructions == ManagedDeveloperInstructions("high", text)
    assert result.policy == compose_mcp_requirements(()).policy


@pytest.mark.parametrize("text", [False, 10, []])
def test_invalid_layer_type_is_not_hidden_by_later_valid_value(text):
    with pytest.raises(ValueError, match="low.*must be a string"):
        compose_mcp_requirements((layer("low", text), layer("high", "OK")))


def test_exact_utf8_budget_includes_markers_and_replacement_notice():
    available = 40_000 - len(render(REPLACEMENT + "\n\n").encode("utf-8"))
    text = "界" * (available // 3) + "x" * (available % 3)
    assert compose_mcp_requirements((layer("host", text),)).developer_instructions.text == text
    with pytest.raises(ValueError, match="host.*10000"):
        compose_mcp_requirements((layer("host", text + "x"),))
