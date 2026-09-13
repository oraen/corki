from pathlib import Path

import pytest

from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.config.plugins import LayerDisabledPlugins, LayerPluginDirectories, plugin_selection


@pytest.mark.parametrize("invalid", [0, 1, "false", None, []])
def test_plugins_feature_requires_boolean(tmp_path, invalid):
    with pytest.raises(ValueError, match="features.plugins"):
        CorkiSettings(working_directory=tmp_path, plugins_enabled=invalid)


def test_plugin_sources_are_explicit_even_when_host_values_equal_files(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[plugins]\ndirectories=["extra"]\ndisabled=["one"]\n')
    loaded = CorkiSettings.for_directory(tmp_path, config_file=config)
    assert loaded.plugin_dirs == (tmp_path / "extra",)
    assert loaded.disabled_plugins == frozenset({"one"})
    assert isinstance(loaded.plugin_dirs, LayerPluginDirectories)
    assert isinstance(loaded.disabled_plugins, LayerDisabledPlugins)
    host = CorkiSettings(
        working_directory=tmp_path,
        plugin_dirs=(tmp_path / "extra",),
        disabled_plugins=frozenset({"one"}),
    )
    assert not isinstance(host.plugin_dirs, LayerPluginDirectories)
    assert not isinstance(host.disabled_plugins, LayerDisabledPlugins)
    defaults = CorkiSettings(working_directory=tmp_path)
    assert isinstance(defaults.disabled_plugins, LayerDisabledPlugins)


def test_plugin_selection_uses_captured_project_layers_and_source_relative_paths(tmp_path):
    config = LocalConfigState(
        (
            ConfigLayer(
                tmp_path / "home/config.toml",
                "user",
                contents='[plugins]\ndirectories=["user"]\ndisabled=["one"]\n',
            ),
            ConfigLayer(
                tmp_path / "project/config.toml",
                "project",
                contents='[plugins]\ndirectories=["project"]\n',
            ),
            ConfigLayer(
                tmp_path / "disabled/config.toml",
                "project",
                disabled_reason="untrusted",
                contents='[plugins]\ndisabled=["two"]\n',
            ),
        )
    )
    directories, disabled = plugin_selection(config)
    assert tuple(map(Path, directories)) == (tmp_path / "home/user",)
    assert disabled == frozenset({"one"})


def test_raw_layer_invalid_package_map_is_not_cold_startup_validation(tmp_path, caplog):
    config = LocalConfigState(
        (
            ConfigLayer(
                tmp_path / "config.toml",
                "user",
                contents=(
                    '[plugins]\ndisabled=["legacy"]\n'
                    "[plugins.good]\nenabled=false\n"
                    '[plugins.bad]\nenabled="false"\n'
                ),
            ),
        )
    )
    # Native raw-layer service rejects the whole typed mapping, not just the
    # malformed sibling. Corki's explicit legacy selector remains independent.
    assert plugin_selection(config) == ([], frozenset({"legacy"}))
    assert "ignoring package policy mapping" in caplog.text
    with pytest.raises(ValueError, match="plugins.bad.enabled"):
        plugin_selection(config, strict=True)
