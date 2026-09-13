"""Ordinary ToolInfo identity, opaque metadata, search and collision contracts."""

from copy import deepcopy
from hashlib import sha1

import pytest

from corki.mcp.names import normalize_tool_names
from corki.mcp.projection import project_tool
from corki.mcp.tools import MCPTool


@pytest.mark.parametrize(
    "raw,meta",
    [
        ("Gmail_Search", {"connector_name": "Gmail"}),
        ("Gmail", {"connector_name": "Gmail"}),
        ("GmailSearch", {"connector_name": "Gmail"}),
        (
            "id_Search",
            {"connector_id": "id", "connector_name": "Mail"},
        ),
        ("  Foo!BAR  ", {}),
        ("你好", {}),
        ("APP_more", {"connector_name": "你好"}),
        ("Gmail__Search", {"connector_name": "Gmail"}),
        ("Gmail_Search", {"connector_name": "\u001cGmail"}),
        ("ABC", {"connector_name": False}),
    ],
)
def test_connector_metadata_never_rewrites_raw_identity(raw, meta):
    projection = project_tool("codex_apps", {"name": raw, "_meta": meta})
    assert projection.identity.remote == raw
    assert projection.identity.leaf == raw
    assert projection.identity.namespace == "codex_apps"


@pytest.mark.parametrize("value", [None, False, 5, [], {}, "", " \u2003"])
def test_invalid_or_empty_primary_metadata_cannot_activate_string_alias(value):
    projected = project_tool(
        "codex_apps",
        {
            "name": "Gmail_Read",
            "_meta": {
                "connector_id": value,
                "connector_name": value,
                "connector_display_name": "\u2003Gmail\u2003",
                "connector_description": value,
                "connectorDescription": " Mail ",
            },
        },
    )
    assert projected.identity.connector_id is None
    assert projected.connector_name is None
    assert projected.namespace_description is None
    assert projected.definition["_meta"] == {}


@pytest.mark.parametrize("server", ["ordinary", "Codex_Apps", "codex_apps_extra", "codex_apps"])
def test_all_servers_strip_reserved_fields_and_detach_projection(server):
    raw = {
        "name": "Gmail_Read",
        "title": "Gmail_Read invoices",
        "_meta": {
            "connector_id": " gmail ",
            "connector_name": " Gmail ",
            "connector_display_name": "ignored",
            "connector_description": " Mail ",
            "connectorDescription": "ignored",
            "connectorFutureField": "future",
            "CONNECTOR_UPPERCASE": "uppercase",
            "openai/fileParams": ["file"],
            "custom": "kept",
        },
    }
    original = deepcopy(raw)
    projected = project_tool(server, raw, "Server instructions")
    assert raw == original
    assert projected.definition["_meta"] == {
        "connectorFutureField": "future",
        "CONNECTOR_UPPERCASE": "uppercase",
        "openai/fileParams": ["file"],
        "custom": "kept",
    }
    assert projected.definition["title"] == raw["title"]
    assert projected.identity.connector_id is None and projected.connector_name is None
    assert projected.namespace_description == "Server instructions"
    projected.definition["_meta"]["openai/fileParams"].append("other")
    assert raw == original


@pytest.mark.parametrize(
    "title",
    [
        "Gmail_Read",
        "gmail_Read",
        "Gmail_",
        " Gmail_Read",
        "GmailRead",
    ],
)
def test_title_remains_verbatim(title):
    assert (
        project_tool(
            "codex_apps", {"name": "read", "title": title, "_meta": {"connector_name": " Gmail "}}
        ).definition["title"]
        == title
    )


def test_search_text_source_and_model_description_have_distinct_fallbacks():
    tool = MCPTool(
        "codex_apps",
        {
            "name": "Gmail_Search",
            "title": "Gmail_Search invoices",
            "description": "Find mail",
            "_meta": {"connector_id": "gmail", "connector_name": "Gmail"},
            "inputSchema": {"type": "object", "properties": {"z": {}, "a": {}}},
        },
        object(),
        server_instructions="Not connector instructions",
    )
    assert tool.spec.source == "codex_apps"
    assert tool.spec.source_description == "Not connector instructions"
    assert tool.spec.namespace_description == "Not connector instructions"
    assert tool.spec.search_text == (
        "mcp__codex_appsGmail_Search Gmail_Search Gmail_Search codex_apps "
        "Gmail_Search invoices Find mail Not connector instructions a z"
    )


@pytest.mark.parametrize("prefix,exceptions", [(True, ()), (False, ()), (True, ("codex_apps",))])
def test_dedup_ignores_connector_id_but_hashes_raw_name_collisions(prefix, exceptions):
    definitions = [
        {"name": remote, "_meta": {"connector_id": connector, "connector_name": "Gmail"}}
        for connector, remote in [
            ("a", "Gmail_Search"),
            ("b", "Gmail_Search"),
            ("a", "Gmail-Search"),
            ("a", "Gmail_Search"),
        ]
    ]
    identities = [project_tool("codex_apps", d).identity for d in definitions]
    names = normalize_tool_names(identities, prefix=prefix, non_prefixed_servers=exceptions)
    assert names == normalize_tool_names(
        reversed(identities), prefix=prefix, non_prefixed_servers=exceptions
    )
    assert len(names) == 2 and len({name.canonical for name in names}) == 2
    for name in names:
        assert name.identity.connector_id is None
        assert name.namespace == ("mcp__" if prefix and not exceptions else "") + "codex_apps"
        leaf = (
            "Gmail_Search_"
            + sha1(name.identity.raw_identity.encode(), usedforsecurity=False).hexdigest()[:12]
        )
        assert name.leaf == leaf


def test_long_connector_name_cannot_change_name_budget():
    projected = project_tool("codex_apps", {"name": "Read", "_meta": {"connector_name": "x" * 250}})
    (name,) = normalize_tool_names((projected.identity,))
    assert name.canonical == "mcp__codex_apps::Read"
    assert name.remote == "Read"
