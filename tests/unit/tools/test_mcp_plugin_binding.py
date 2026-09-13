"""Host MCP plugin attribution is frozen with the selected Step binding."""

from dataclasses import replace

import pytest

from corki.mcp.tools import MCPTool
from corki.protocol.tools import ToolSpec
from corki.tools import ToolRegistry
from corki.tools.registry import ToolSource


def tool(plugin):
    return MCPTool(
        "server", {"name": "lookup", "_meta": {"plugin_id": "forged"}}, object(), plugin_id=plugin
    )


@pytest.mark.parametrize("sealed", [False, True])
@pytest.mark.parametrize("external", [False, True])
def test_publication_freezes_provenance_and_replacement_can_remove_it(sealed, external):
    registry = ToolRegistry()
    owner = registry.create_owner(source=ToolSource.MCP if external else None)
    original = tool("original")
    registry.replace_owned(owner, (original,))
    if sealed:
        registry.seal()
    first = registry.snapshot()
    name = original.spec.name
    # Even a mutable host handler cannot rewrite an already captured Step.
    original._plugin_id = "mutated"
    assert first.mcp_plugin_id(name) == "original"
    assert (
        first.derive(specs=(replace(original.spec, description="new schema hint"),)).mcp_plugin_id(
            name
        )
        == "original"
    )
    registry.replace_owned(owner, (tool(None),))
    assert registry.snapshot().mcp_plugin_id(name) is None
    assert first.mcp_plugin_id(name) == "original"
    assert first.derive(tools=(tool(None),)).mcp_plugin_id(name) is None
    assert first.derive(remove=frozenset({name})).mcp_plugin_id(name) is None


def test_shadowed_external_candidates_keep_their_own_frozen_attribution():
    registry = ToolRegistry()
    mcp = registry.create_owner(source=ToolSource.MCP)
    extension = registry.create_owner(source=ToolSource.EXTENSION)
    registry.replace_owned(mcp, (tool("mcp-plugin"),))
    alternate = tool("extension-plugin")
    registry.replace_owned(extension, (alternate,))
    snapshot = registry.snapshot()
    alternate._plugin_id = "changed-after-capture"
    name = alternate.spec.name
    assert snapshot.mcp_plugin_id(name) == "mcp-plugin"
    assert snapshot.without_owners(frozenset({mcp})).mcp_plugin_id(name) == "extension-plugin"


@pytest.mark.parametrize("invalid", [True, "", [], {}])
def test_invalid_host_attribution_cannot_partially_publish(invalid):
    registry = ToolRegistry()
    owner = registry.create_owner()
    registry.replace_owned(owner, (tool("original"),))
    registry.seal()
    snapshot = registry.snapshot()
    bad = tool(None)
    bad._plugin_id = invalid
    with pytest.raises(ValueError, match="MCP plugin attribution"):
        registry.replace_owned(owner, (bad,))
    assert registry.snapshot() is snapshot


def test_spec_source_and_remote_metadata_are_not_host_attribution():
    registry = ToolRegistry()
    plain_mcp = tool(None)
    registry.register(plain_mcp)

    class Ordinary:
        spec = ToolSpec("ordinary", "unrelated", {}, source="claimed-plugin")

    registry.register(Ordinary())
    snapshot = registry.snapshot()
    assert snapshot.mcp_plugin_id(plain_mcp.spec.name) is None
    assert snapshot.mcp_plugin_id("ordinary") is None
