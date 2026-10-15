from dataclasses import replace

import pytest

from corki.mcp.names import normalize_tool_names, sanitize
from corki.mcp.tools import MCPTool


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("server.one", "server_one"),
        ("tool-two", "tool_two"),
        ("你好", "__"),
        ("a_12", "a_12"),
        ("", "_"),
    ],
)
def test_source_ascii_sanitizer(raw, expected):
    assert sanitize(raw) == expected


@pytest.mark.parametrize(
    "prefix,exceptions,expected",
    [
        (True, (), ("mcp__calendar", "mcp__history")),
        (True, ("history",), ("mcp__calendar", "history")),
        (False, (), ("calendar", "history")),
        (False, ("history",), ("calendar", "history")),
    ],
)
def test_source_namespace_prefixes(prefix, exceptions, expected):
    result = normalize_tool_names(
        (("history", "read"), ("calendar", "read")), prefix=prefix, non_prefixed_servers=exceptions
    )
    assert tuple(r.namespace for r in result) == expected
    assert normalize_tool_names((("mcp__history", "read"),))[0].namespace == "mcp__history"


def test_source_dedup_and_collision_disambiguation_are_order_independent():
    raw = (
        ("basic-server", "tool-name"),
        ("basic_server", "tool_name"),
        ("basic-server", "tool_name"),
        ("basic-server", "tool-name"),
    )
    result = normalize_tool_names(raw)
    assert result == normalize_tool_names(reversed(raw))
    assert len(result) == 3 and len({r.namespace for r in result}) == 2
    assert len({r.canonical for r in result}) == 3
    assert all(r.namespace.startswith("mcp__basic_server_") for r in result)
    assert all(len(r.namespace.rsplit("_", 1)[1]) == 12 for r in result)
    tools = [r for r in result if r.server == "basic-server"]
    assert len({r.leaf for r in tools}) == 2
    assert all(r.leaf.startswith("tool_name_") for r in tools)


@pytest.mark.parametrize(
    "server,remote",
    [("server", "a" * 115), ("server", "a" * 128), ("s" * 200, "read"), ("s" * 200, "t" * 200)],
)
def test_source_combined_length_bound(server, remote):
    (name,) = normalize_tool_names(((server, remote),))
    assert len(name.namespace) + 2 + len(name.leaf) <= 128
    assert (name.server, name.remote) == (server, remote)
    assert name == normalize_tool_names(((server, remote),))[0]


def test_source_exact_length_boundary_and_concatenated_identity_collision():
    server = "server"
    limit = 128 - len("mcp__server__")
    first = normalize_tool_names(((server, "a" * limit),))[0]
    second = normalize_tool_names(((server, "a" * (limit + 1)),))[0]
    assert first.leaf == "a" * limit and len(first.namespace) + 2 + len(first.leaf) == 128
    assert len(second.namespace) + 2 + len(second.leaf) == 128 and second.leaf != "a" * limit
    result = normalize_tool_names((("ab", "c"), ("a", "bc")), prefix=False)
    assert len({r.namespace + r.leaf for r in result}) == 2


def test_namespace_description_byte_cap_and_rebinding_preserve_frozen_metadata():
    tool = MCPTool(
        "docs",
        {"name": "read-file", "description": "needle", "inputSchema": {}},
        object(),
        server_instructions="🦀" * 131073,
    )
    original = tool.spec
    assert original.name == "mcp__docs::read_file"
    assert len(original.namespace_description.encode()) == 512 * 1024
    assert len(original.source_description) == 131073
    rebound = tool.with_model_name(normalize_tool_names((("docs", "read-file"),), prefix=False)[0])
    assert tool.spec == original and rebound.spec.name == "docs::read_file"
    assert rebound.spec.search_text.startswith("docsread_file read_file read-file docs ")
    with pytest.raises(ValueError, match="raw route"):
        tool.with_model_name(
            replace(normalize_tool_names((("docs", "read-file"),))[0], remote="other")
        )
