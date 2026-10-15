"""Serde typed envelopes and Value subtrees are deliberately different boundaries."""

import json

import pytest

from corki.plugins import PluginManager
from corki.tools import ToolRegistry

SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
RAW = "$serde_json::private::RawValue"
NUMBER = "$serde_json::private::Number"


def discover(root):
    return PluginManager.discover_and_load(
        roots=(root,), disabled=frozenset(), registry=ToolRegistry()
    )


def test_root_manifest_is_a_value_and_expands_private_raw_value(tmp_path):
    (tmp_path / "plugin.json").write_text(
        json.dumps({RAW: json.dumps({"$schema": SCHEMA, "name": "sample"})})
    )
    manager = discover(tmp_path)
    assert [p.manifest.name for p in manager.plugins] == ["sample"]
    assert manager.plugins[0].manifest.agent_plugin


def test_invalid_private_value_root_cannot_claim_agent_identity(tmp_path):
    (tmp_path / "plugin.json").write_text(
        json.dumps({NUMBER: "1", "$schema": SCHEMA, "name": "sample"})
    )
    legacy = tmp_path / ".codex-plugin/plugin.json"
    legacy.parent.mkdir()
    legacy.write_text('{"name":"legacy"}')
    assert [p.manifest.name for p in discover(tmp_path).plugins] == ["legacy"]


def test_mcp_server_value_expands_raw_value_before_validation(tmp_path):
    (tmp_path / "plugin.json").write_text(json.dumps({"$schema": SCHEMA, "name": "sample"}))
    (tmp_path / "mcp.json").write_text(
        json.dumps(
            {
                "$schema": MCP_SCHEMA,
                "mcpServers": {
                    "docs": {RAW: json.dumps({"type": "stdio", "command": "python"})},
                },
            }
        )
    )
    manager = discover(tmp_path)
    assert manager.warnings == ()
    assert [r.settings.name for r in manager.mcp_registrations] == ["docs"]


def test_mcp_typed_envelope_does_not_expand_private_raw_value(tmp_path):
    (tmp_path / "plugin.json").write_text(json.dumps({"$schema": SCHEMA, "name": "sample"}))
    (tmp_path / "mcp.json").write_text(
        json.dumps(
            {
                RAW: json.dumps(
                    {
                        "$schema": MCP_SCHEMA,
                        "mcpServers": {"docs": {"type": "stdio", "command": "python"}},
                    }
                )
            }
        )
    )
    manager = discover(tmp_path)
    assert len(manager.plugins) == 1
    assert manager.mcp_registrations == ()
    assert manager.warnings


def test_mcp_typed_server_map_preserves_private_looking_server_name(tmp_path):
    (tmp_path / "plugin.json").write_text(json.dumps({"$schema": SCHEMA, "name": "sample"}))
    (tmp_path / "mcp.json").write_text(
        json.dumps(
            {
                "$schema": MCP_SCHEMA,
                "mcpServers": {
                    RAW: {"type": "stdio", "command": "python"},
                },
            }
        )
    )
    manager = discover(tmp_path)
    assert manager.warnings == ()
    assert [r.settings.name for r in manager.mcp_registrations] == [RAW]


@pytest.mark.parametrize("field", ["skills", "hooks", "mcpServers", "defaultPrompt"])
def test_overlay_buffered_fields_reject_invalid_unicode(tmp_path, field):
    (tmp_path / "plugin.json").write_text(json.dumps({"$schema": SCHEMA, "name": "sample"}))
    overlay = tmp_path / ".codex-plugin/plugin.json"
    overlay.parent.mkdir()
    data = {"interface": {field: "\ud800"}} if field == "defaultPrompt" else {field: "\ud800"}
    overlay.write_text(json.dumps(data))
    manager = discover(tmp_path)
    assert manager.plugins == ()
    assert manager.warnings


def test_overlay_ignored_fields_do_not_decode_unused_unicode(tmp_path):
    (tmp_path / "plugin.json").write_text(json.dumps({"$schema": SCHEMA, "name": "sample"}))
    overlay = tmp_path / ".codex-plugin/plugin.json"
    overlay.parent.mkdir()
    overlay.write_text(json.dumps({"future": "\ud800"}))
    assert [p.manifest.name for p in discover(tmp_path).plugins] == ["sample"]


@pytest.mark.parametrize("field", ["skills", "defaultPrompt"])
def test_overlay_invalid_value_fallback_rejects_malformed_private_json(tmp_path, field):
    (tmp_path / "plugin.json").write_text(json.dumps({"$schema": SCHEMA, "name": "sample"}))
    overlay = tmp_path / ".codex-plugin/plugin.json"
    overlay.parent.mkdir()
    data = {field: {RAW: "not-json"}}
    overlay.write_text(json.dumps({"interface": data} if field == "defaultPrompt" else data))
    assert discover(tmp_path).plugins == ()


def test_overlay_mcp_object_decodes_each_server_value_before_env_merge(tmp_path):
    (tmp_path / "plugin.json").write_text(json.dumps({"$schema": SCHEMA, "name": "sample"}))
    (tmp_path / "mcp.json").write_text(
        json.dumps(
            {
                "$schema": MCP_SCHEMA,
                "mcpServers": {"docs": {"type": "stdio", "command": "portable"}},
            }
        )
    )
    overlay = tmp_path / ".codex-plugin/plugin.json"
    overlay.parent.mkdir()
    overlay.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "docs": {RAW: json.dumps({"command": "ignored", "env_vars": ["TOKEN"]})}
                }
            }
        )
    )
    manager = discover(tmp_path)
    assert manager.mcp_registrations[0].settings.command == "portable"
    assert manager.mcp_registrations[0].settings.env_vars == ("TOKEN",)
