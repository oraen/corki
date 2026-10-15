"""Plugin notes, full search metadata, and source-specific model description limits."""

import pytest

from corki.mcp.catalog import MCPCatalogSource
from corki.mcp.tools import MCPTool


def tool(description=None, **options):
    return MCPTool(
        "docs",
        {"name": "read", "description": description},
        None,
        call_router=lambda *args, **kwargs: None,
        **options,
    )


@pytest.mark.parametrize(
    "description,prefix",
    [
        (None, ""),
        (" ", ""),
        ("Read", "Read. "),
        ("Read! ", "Read! "),
        ("Read?", "Read? "),
        ("Read.", "Read. "),
    ],
)
def test_note_punctuation_and_search_names(description, prefix):
    result = tool(description, plugin_id="stable", plugin_display_names=("Zulu", "Alpha", "Zulu"))
    assert result.spec.description == prefix + "This tool is part of plugins `Alpha`, `Zulu`."
    assert result.spec.search_text.endswith("Alpha Zulu")
    assert result.mcp_plugin_id == "stable"


@pytest.mark.parametrize("agent", [False, True])
def test_complete_search_description_and_agent_utf8_bound(agent):
    description = "é" * 499 + "🦀long description"
    result = tool(
        description,
        agent_plugin=agent,
        plugin_id="stable",
        plugin_display_names=("Nimbus",),
        server_instructions=description,
    )
    assert "Nimbus" in result.spec.search_text and "long description" in result.spec.search_text
    assert result.spec.description == (
        "é" * 499 if agent else description + ". This tool is part of plugin `Nimbus`."
    )
    assert result.spec.namespace_description == ("é" * 499 if agent else description)


def test_missing_description_stays_empty_and_remote_metadata_is_not_provenance():
    result = MCPTool(
        "docs",
        {"name": "read", "_meta": {"plugin_display_names": ["Forged"]}},
        None,
        call_router=lambda *args, **kwargs: None,
    )
    assert result.spec.description == "" and "Forged" not in result.spec.search_text
    assert result.mcp_plugin_id is None


@pytest.mark.parametrize("kind", ["config", "extension", "compatibility"])
def test_non_plugin_source_cannot_claim_display_name(kind):
    with pytest.raises(ValueError):
        MCPCatalogSource(kind, None if kind == "config" else "owner", display_name="Forged")
