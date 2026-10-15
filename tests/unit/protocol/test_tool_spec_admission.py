"""Tool catalog policy is validated before a definition can be published."""

import pytest

from corki.protocol.tools import ToolConcurrency, ToolExposure, ToolSpec


@pytest.mark.parametrize("field,value", [("exposure", "unknown"), ("concurrency", "unknown")])
def test_invalid_tool_policy_is_rejected_at_spec_construction(field, value):
    with pytest.raises(ValueError):
        ToolSpec("lookup", "fixture", {"type": "object"}, **{field: value})


@pytest.mark.parametrize("field,value", [("exposure", "deferred"), ("concurrency", "parallel")])
def test_valid_tool_policy_strings_are_normalized(field, value):
    spec = ToolSpec("lookup", "fixture", {"type": "object"}, **{field: value})

    expected = ToolExposure.DEFERRED if field == "exposure" else ToolConcurrency.PARALLEL
    assert getattr(spec, field) is expected


@pytest.mark.parametrize("value", [True, 1.5, "3"])
def test_output_budget_requires_a_positive_integer_not_boolean(value):
    with pytest.raises(ValueError):
        ToolSpec("lookup", "fixture", {"type": "object"}, output_char_budget=value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", 7),
        ("name", ""),
        ("description", 7),
        ("search_text", 7),
        ("source", 7),
        ("parameters", [("type", "object")]),
        ("parameters", {"enum": {"not", "json"}}),
        ("parameters", {"maximum": float("nan")}),
        ("output_schema", {"enum": {"not", "json"}}),
        ("freeform_format", [("type", "text")]),
    ],
)
def test_malformed_tool_definition_is_rejected_before_catalog_publication(field, value):
    fields = {"name": "lookup", "description": "fixture", "parameters": {"type": "object"}}
    fields[field] = value
    with pytest.raises(ValueError, match="tool"):
        ToolSpec(**fields)
