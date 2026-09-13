"""Ordinary approvals follow admitted declarations, never legacy Apps grants."""

from pathlib import Path

import pytest

from corki.config.layers import ConfigLayer, LocalConfigState
from corki.mcp.approval_persistence import MCPApprovalPersistence
from corki.mcp.catalog import MCPCatalogSource


@pytest.mark.parametrize("name", ["ordinary", "codex_apps"])
@pytest.mark.parametrize("source", ["user", "project", "plugin", "legacy_apps_only"])
def test_persistence_target_uses_ordinary_source(tmp_path, name, source):
    user, project = tmp_path / "user.toml", tmp_path / "project.toml"
    legacy = '[apps.fixture.tools.write]\napproval_mode="approve"\n'
    declaration = f'[mcp.servers.{name}]\nurl="https://fixture.invalid"\n'
    configuration = LocalConfigState(
        (
            ConfigLayer(user, "user", contents=legacy + (declaration if source == "user" else "")),
            ConfigLayer(project, "project", contents=declaration if source == "project" else ""),
        )
    )
    persistence = MCPApprovalPersistence(configuration, Path(tmp_path))
    key = (name, "fixture", "old-account", "write")
    provenance = MCPCatalogSource("plugin", "package") if source == "plugin" else None
    if source == "legacy_apps_only":
        with pytest.raises(ValueError, match="no configured persistence destination"):
            persistence.target(key, configuration, provenance)
    else:
        target, segments = persistence.target(key, configuration, provenance)
        assert target == (project if source == "project" else user)
        assert segments == (
            ("plugins", "package", "mcp_servers", name, "tools", "write", "approval_mode")
            if source == "plugin"
            else ("mcp", "servers", name, "tools", "write", "approval_mode")
        )
