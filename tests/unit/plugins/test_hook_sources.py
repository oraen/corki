"""Legacy hook snapshots preserve identity without enabling Agent Plugin overlays."""

import json
import os
from pathlib import Path

import pytest

from corki.plugins.hooks import load_plugin_hooks
from corki.plugins.installed_manifest import installed_manifest
from corki.plugins.store import PluginId, PluginInstallation


@pytest.mark.skipif(os.name != "nt", reason="Windows path anchors")
@pytest.mark.parametrize("selection", ["./\\escape.json", "./C:escape.json", "./Z:escape.json"])
def test_windows_anchored_hook_path_is_rejected_before_read(tmp_path, monkeypatch, selection):
    def unexpected_read(*args, **kwargs):
        pytest.fail("anchored hook path must be rejected before reading")

    monkeypatch.setattr(Path, "read_text", unexpected_read)
    sources, warnings = load_plugin_hooks(tmp_path, selection)
    assert sources == ()
    assert warnings == ("ignoring hooks: expected a contained ./ path",)


def test_bad_hook_file_preserves_valid_sibling(tmp_path):
    good = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo ok"}]}]}}
    (tmp_path / "good.json").write_text(json.dumps(good))
    (tmp_path / "bad.json").write_text("{")
    sources, warnings = load_plugin_hooks(tmp_path, ["./bad.json", "./good.json"])
    assert [source.relative_path for source in sources] == ["good.json"]
    assert len(warnings) == 1
    (tmp_path / "good.json").write_text("{}")
    assert json.loads(sources[0].contents) == good


@pytest.mark.parametrize("agent", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
def test_installed_hook_sources_use_installation_data_root_and_format_boundary(
    tmp_path, agent, enabled
):
    root = tmp_path / "package"
    (root / ".codex-plugin").mkdir(parents=True)
    (root / "hooks").mkdir()
    hooks = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo ok"}]}]}}
    (root / "hooks/hooks.json").write_text(json.dumps(hooks))
    (root / ".codex-plugin/plugin.json").write_text(
        json.dumps({"name": "display-name", "hooks": hooks})
    )
    if agent:
        (root / "plugin.json").write_text(
            json.dumps(
                {
                    "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                    "name": "display-name",
                }
            )
        )
    identity = PluginId.parse("installed@market")
    manifest = installed_manifest(
        PluginInstallation(identity.key, root, enabled, identity), tmp_path
    )
    assert manifest.error is None
    assert manifest.identity == identity.key
    if enabled and not agent:
        assert len(manifest.hook_sources) == 1
        assert manifest.hook_sources[0].relative_path == "plugin.json#hooks[0]"
        assert manifest.hook_sources[0].data_root == identity.data_root(
            tmp_path, agent_plugin=False
        )
    else:
        assert manifest.hook_sources == ()
