"""Native installed Agent MCP data ownership and transport-local degradation."""

import json

import pytest

from corki.plugins.agent_manifest import parse_agent_manifest
from corki.plugins.installed_manifest import installed_manifest
from corki.plugins.manifest_path import AGENT_SCHEMA
from corki.plugins.store import PluginId, PluginInstallation


def agent_installation(tmp_path, *, stdio=True, enabled=True):
    root = tmp_path / "plugins/cache/c/a-b/local"
    root.mkdir(parents=True)
    (root / "plugin.json").write_text(json.dumps({"$schema": AGENT_SCHEMA, "name": "a-b"}))
    servers = {"remote": {"type": "streamable-http", "url": "https://fixture.invalid/mcp"}}
    if stdio:
        servers["local"] = {"type": "stdio", "command": "echo"}
    (root / "mcp.json").write_text(
        json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
                "mcpServers": servers,
            }
        )
    )
    plugin_id = PluginId.parse("a-b@c")
    return PluginInstallation("a-b@c", root, enabled, plugin_id, "local")


def test_installed_agent_creates_native_isolated_data_directory(tmp_path):
    installation = agent_installation(tmp_path)
    manifest = installed_manifest(installation, tmp_path)
    expected = tmp_path / (
        "plugins/data/agent-plugins/"
        "6920dd17774030852d11d1b94758fcaae4f894c7b2f36301ed174bc3b33e0743"
    )
    assert expected.is_dir()
    local = next(server for server in manifest.mcp_servers if server.transport == "stdio")
    assert dict(local.env)["PLUGIN_DATA"] == str(expected.resolve())
    assert manifest.identity == "a-b@c"
    assert not manifest.mcp_warnings
    assert not (installation.root / ".plugin-data").exists()


def test_data_creation_failure_disables_stdio_only(tmp_path):
    installation = agent_installation(tmp_path)
    data = installation.plugin_id.data_root(tmp_path, agent_plugin=True)
    data.parent.mkdir(parents=True)
    data.write_text("owned file must survive")
    manifest = installed_manifest(installation, tmp_path)
    assert [(server.name, server.transport) for server in manifest.mcp_servers] == [
        ("a-b__remote", "http")
    ]
    assert any("disabling stdio" in warning for warning in manifest.mcp_warnings)
    assert manifest.error is None
    assert data.read_text() == "owned file must survive"


@pytest.mark.parametrize("case", ["disabled", "http_only", "directory_plugin"])
def test_no_unnecessary_installed_data_directory(tmp_path, case):
    installation = agent_installation(
        tmp_path, stdio=case != "http_only", enabled=case != "disabled"
    )
    manifest = (
        parse_agent_manifest(installation.root / "plugin.json")
        if case == "directory_plugin"
        else installed_manifest(installation, tmp_path)
    )
    assert not (tmp_path / "plugins/data").exists()
    assert not (installation.root / ".plugin-data").exists()
    assert manifest.enabled == (case != "disabled")
    assert len(manifest.mcp_servers) == {"disabled": 0, "http_only": 1, "directory_plugin": 2}[case]


def test_native_cache_manifest_does_not_authorize_python_entrypoint(tmp_path):
    root = tmp_path / "plugins/cache/lab/fixture/local"
    (root / ".codex-plugin").mkdir(parents=True)
    (root / ".codex-plugin/plugin.json").write_text(
        json.dumps({"name": "display-namespace", "entrypoint": "plugin.py:register"})
    )
    (root / ".corki-plugin").mkdir()
    (root / ".corki-plugin/plugin.toml").write_text(
        '[plugin]\nname="override"\nentrypoint="plugin.py:register"\n'
    )
    manifest = installed_manifest(
        PluginInstallation("fixture@lab", root, True, PluginId.parse("fixture@lab"), "local"),
        tmp_path,
    )
    assert manifest.name == "display-namespace"
    assert manifest.identity == "fixture@lab"
    assert manifest.entrypoint is None
    assert manifest.error is None
