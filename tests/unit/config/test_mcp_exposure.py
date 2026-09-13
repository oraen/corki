"""Optional MCP masks are host configuration, not remote tool metadata."""

from dataclasses import replace
from itertools import combinations

import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.config.layers import _merge
from corki.mcp.reconciliation import connection_identity


@pytest.mark.parametrize("invalid", [True, "direct", {}, [True], [1], ["hidden"], ["Direct"]])
def test_mask_rejects_invalid_surfaces(invalid):
    with pytest.raises(ValueError, match="omit_tools_from"):
        MCPServerSettings.from_mapping("docs", {"command": "fixture", "omit_tools_from": invalid})


@pytest.mark.parametrize(
    "mask",
    [
        None,
        *(list(c) for n in range(4) for c in combinations(("code_mode", "deferred", "direct"), n)),
        ["direct", "direct"],
    ],
)
def test_mask_config_load_preserves_empty_none_and_copies_arrays(tmp_path, mask):
    config = tmp_path / "config.toml"
    config.write_text(
        '[mcp.servers.docs]\ncommand="fixture"\n'
        + ("" if mask is None else f"omit_tools_from={mask!r}\n")
    )
    (server,) = CorkiSettings.for_directory(tmp_path, config_file=config).mcp_servers
    original = None if mask is None else tuple(mask)
    assert server.omit_tools_from == original
    value = MCPServerSettings("docs", "stdio", command="fixture", omit_tools_from=mask)
    if mask is not None:
        mask.append("mutated")
    assert value.omit_tools_from == original
    assert connection_identity(server) == connection_identity(
        replace(server, omit_tools_from=("code_mode",))
    )


@pytest.mark.parametrize("override", [None, [], ["deferred"]])
def test_omission_array_uses_layer_replace_not_union(override):
    lower = {"mcp": {"servers": {"docs": {"command": "fixture", "omit_tools_from": ["direct"]}}}}
    higher = {} if override is None else {"omit_tools_from": override}
    _merge(lower, {"mcp": {"servers": {"docs": higher}}})
    config = MCPServerSettings.from_mapping("docs", lower["mcp"]["servers"]["docs"])
    assert config.omit_tools_from == (("direct",) if override is None else tuple(override))
