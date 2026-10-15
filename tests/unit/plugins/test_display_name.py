"""Native presentation precedence does not replace package authority."""

import json

import pytest

from corki.plugins import PluginManager
from corki.plugins.manifest_path import AGENT_SCHEMA
from corki.tools import ToolRegistry


@pytest.mark.parametrize("kind", ["legacy", "agent_overlay", "agent_extension"])
@pytest.mark.parametrize(
    "display,expected",
    [(None, "sample"), ("\u2003 \n", "sample"), (" Nimbus ", "Nimbus"), ("\x1c", "\x1c")],
)
def test_manifest_display_name_precedence(tmp_path, kind, display, expected):
    overlay = tmp_path / ".codex-plugin/plugin.json"
    overlay.parent.mkdir()
    overlay.write_text(json.dumps({"name": "sample", "interface": {"displayName": display}}))
    if kind != "legacy":
        value = {"$schema": AGENT_SCHEMA, "name": "sample"}
        if kind == "agent_extension":
            value["extensions"] = {
                "com.openai": {"name": "ignored", "interface": {"displayName": display}}
            }
            overlay.write_text(
                json.dumps({"name": "ignored", "interface": {"displayName": "Losing overlay"}})
            )
        (tmp_path / "plugin.json").write_text(json.dumps(value))
    manager = PluginManager.discover_and_load(
        roots=(tmp_path,), disabled=frozenset(), registry=ToolRegistry()
    )
    assert len(manager.plugins) == 1, manager.warnings
    manifest = manager.plugins[0].manifest
    assert manifest.name == "sample" and manifest.display_name == expected


@pytest.mark.parametrize("value", [False, 12, [], {}])
def test_invalid_display_name_does_not_admit_plugin(tmp_path, value):
    path = tmp_path / ".codex-plugin/plugin.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"name": "sample", "interface": {"displayName": value}}))
    manager = PluginManager.discover_and_load(
        roots=(tmp_path,), disabled=frozenset(), registry=ToolRegistry()
    )
    assert not manager.plugins and manager.warnings
