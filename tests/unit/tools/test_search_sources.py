from dataclasses import replace

import pytest

from corki.context.tokens import estimate_request_tokens, estimate_text_tokens
from corki.mcp.tools import MCPTool
from corki.protocol.tools import (
    ToolExposure,
    ToolSpec,
    tool_spec_from_payload,
    tool_spec_to_payload,
)
from corki.tools import ToolRegistry
from corki.tools.search import ToolSearchIndex, ToolSearchTool


def description_for(specs):
    class Tool:
        def __init__(self, spec):
            self.spec = spec

    registry = ToolRegistry()
    for spec in specs:
        registry.register(Tool(spec))
    return ToolSearchTool(registry).spec.description


def source_section(description):
    return description.split("You have access to tools from the following sources:\n", 1)[1].split(
        "\nSome of the tools may not have been provided to you upfront", 1
    )[0]


def test_sources_deduplicate_sort_and_keep_first_present_description():
    base = ToolSpec("first", "", {}, exposure=ToolExposure.DEFERRED, source="docs")
    specs = (
        base,
        replace(base, name="second", source_description="Search files."),
        replace(base, name="third", source_description="Must not replace the first description."),
        replace(base, name="fourth", source="alpha", source_description=""),
        replace(base, name="fifth", source="alpha", source_description="Ignored."),
        replace(base, name="hidden", source="hidden", exposure=ToolExposure.HIDDEN),
        replace(base, name="direct", source="direct", exposure=ToolExposure.DIRECT),
    )
    assert source_section(description_for(specs)) == "- alpha: \n- docs: Search files."


def test_source_budget_reserves_every_name_and_never_splits_utf8():
    long_description = "🦀" * 20_000
    specs = tuple(
        ToolSpec(
            f"tool_{i}",
            "",
            {},
            exposure=ToolExposure.DEFERRED,
            source=f"source-{i:02}",
            source_description=long_description,
        )
        for i in range(8)
    )
    description = description_for(specs)
    section = source_section(description)
    assert len(section.encode()) <= 512 * 1024
    assert long_description in section
    assert [line[2:].split(": ", 1)[0] for line in section.splitlines()] == [
        f"source-{i:02}" for i in range(8)
    ]
    assert "always use `tool_search`" in description


def test_oversized_source_name_is_skipped_whole_without_hiding_following_names():
    base = ToolSpec("first", "", {}, exposure=ToolExposure.DEFERRED, source="a" * (512 * 1024))
    assert source_section(description_for((base, replace(base, name="last", source="z")))) == "- z"
    assert source_section(description_for(())) == "None currently enabled."


def test_regular_mcp_search_uses_complete_instructions_and_raw_callable_frequency():
    instructions = "é" * 2000 + " searchable_namespace_tail"
    tool = MCPTool(
        " docs ",
        {
            "name": "createEvent",
            "title": " Create event ",
            "description": " Plan an event. ",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "zebra": {"description": "nested_not_indexed"},
                    "attendees": {},
                },
            },
            "_meta": {"connector_name": "Untrusted app", "namespace_description": "Spoofed"},
        },
        object(),
        exposure=ToolExposure.DEFERRED,
        server_instructions=" " + instructions + " ",
    )
    assert tool.spec.search_text == (
        "mcp___docs___createEvent createEvent createEvent  docs  Create event Plan an event. "
        + instructions
        + " attendees zebra"
    )
    assert tool.spec.source == "docs" and tool.spec.source_description == instructions
    assert ToolSearchIndex().search((tool.spec,), "searchable_namespace_tail", 8) == (tool.spec,)
    assert source_section(description_for((tool.spec,))) == "- docs: " + instructions


@pytest.mark.parametrize("instructions", [None, "", " \n\t "])
def test_blank_mcp_instructions_do_not_invent_source_description(instructions):
    tool = MCPTool("docs", {"name": "read"}, object(), server_instructions=instructions)
    assert tool.spec.source_description is None


@pytest.mark.parametrize("input_kind", ["json", "freeform"])
def test_source_description_persistence_retains_legacy_payload_shape(input_kind):
    old = {
        "name": "lookup",
        "description": "Lookup",
        "parameters": {},
        "exposure": "deferred",
        "concurrency": "exclusive",
        "output_char_budget": None,
        "search_text": None,
        "source": "docs",
    }
    if input_kind == "freeform":
        old.update(input_kind="freeform", freeform_format={"type": "text"})
    spec = tool_spec_from_payload(old)
    assert tool_spec_to_payload(spec) == old
    with_source = replace(spec, source_description="Search files.")
    payload = tool_spec_to_payload(with_source)
    assert payload == {**old, "source_description": "Search files."}
    assert tool_spec_from_payload(payload) == with_source


def test_source_inventory_is_charged_to_full_request_budget():
    metadata = "🦀" * 2000
    with_source = description_for(
        (
            ToolSpec(
                "lookup",
                "",
                {},
                exposure=ToolExposure.DEFERRED,
                source="docs",
                source_description=metadata,
            ),
        )
    )
    without_source = description_for(())
    assert estimate_request_tokens("", (), (ToolSpec("tool_search", with_source, {}),)) >= (
        estimate_request_tokens("", (), (ToolSpec("tool_search", without_source, {}),))
        + estimate_text_tokens(metadata)
        - 10
    )
