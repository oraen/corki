"""Native app-only declarations never enter the model-facing tool surface."""

import pytest

from corki.mcp.names import normalize_tool_names
from corki.mcp.tool_definition import tool_is_model_visible
from corki.mcp.tools import MCPTool
from corki.protocol.tools import ToolExposure


@pytest.mark.parametrize(
    "metadata,visible",
    [
        (None, True),
        ([], True),
        ({}, True),
        ({"ui": []}, True),
        ({"ui": {"visibility": "app"}}, True),
        ({"ui": {"visibility": []}}, False),
        ({"ui": {"visibility": ["app"]}}, False),
        ({"ui": {"visibility": ["MODEL"]}}, False),
        ({"ui": {"visibility": [None, {}, 1]}}, False),
        ({"ui": {"visibility": ["app", "model"]}}, True),
    ],
)
def test_source_visibility_shape_and_exact_target(metadata, visible):
    definition = {"name": "lookup", "_meta": metadata}
    assert tool_is_model_visible(definition) is visible
    tool = MCPTool("docs", definition, None, call_router=lambda *args: None)
    assert tool.model_visible is visible
    assert (tool.spec.exposure == ToolExposure.DIRECT) is visible
    rebound = tool.with_model_name(
        normalize_tool_names((("docs", "lookup"),))[0], exposure=ToolExposure.DEFERRED
    )
    assert rebound.spec.exposure == (ToolExposure.DEFERRED if visible else ToolExposure.HIDDEN)


def test_absent_client_requires_late_admission_router():
    with pytest.raises(ValueError, match="admission router"):
        MCPTool("docs", {"name": "lookup"}, None)
