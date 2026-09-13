"""Installation selection follows native store and Rust semver ordering."""

import pytest

from corki.config.layers import ConfigLayer, LocalConfigState
from corki.plugins.store import PluginId, PluginStoreSource, active_version, compare_versions


@pytest.mark.parametrize(
    "versions, expected",
    [
        (["9.0.0", "10.0.0"], "10.0.0"),
        (["local", "99.0.0"], "local"),
        (["0123456789abcdef", "fedcba9876543210"], "fedcba9876543210"),
        (["1.0.0-alpha", "1.0.0"], "1.0.0"),
        (["1.0.0+2", "1.0.0+10"], "1.0.0+10"),
        ([], None),
    ],
)
def test_native_active_version_rules(tmp_path, versions, expected):
    for version in versions:
        (tmp_path / version).mkdir()
    (tmp_path / "999.0.0").write_text("not a directory")
    (tmp_path / "9999.0.0").symlink_to(tmp_path, target_is_directory=True)
    (tmp_path / "bad version").mkdir()
    (tmp_path / "版本").mkdir()
    assert active_version(tmp_path) == expected


@pytest.mark.parametrize(
    "left,right",
    [
        ("1.0.0-alpha", "1.0.0-alpha.1"),
        ("1.0.0-2", "1.0.0-alpha"),
        ("1.0.0-9", "1.0.0-10"),
        ("1.0.0", "1.0.0+0"),
        ("1.0.0+0", "1.0.0+00"),
        ("1.0.0+001", "1.0.0+2"),
        ("1.0.0+02", "1.0.0+002"),
        ("1.0.0+002", "1.0.0+10"),
        ("1.0.0+10", "1.0.0+alpha"),
        ("10.0.0-01", "9.0.0"),  # Invalid prerelease switches this pair to lexical.
        ("18446744073709551616.0.0", "9.0.0"),  # Rust u64 overflow is not SemVer.
    ],
)
def test_version_order_and_reverse(left, right):
    assert compare_versions(left, right) == -1
    assert compare_versions(right, left) == 1
    assert compare_versions(left, left) == 0


@pytest.mark.parametrize(
    "identity", ["a", "a@", "@b", "../a@b", "a@../b", "a..b@c", "a@b.c", "a@b@c"]
)
def test_plugin_id_cannot_escape_cache_segments(identity):
    with pytest.raises(ValueError):
        PluginId.parse(identity)


def test_installation_admission_uses_effective_layers_not_cache_presence(tmp_path):
    for market in ("first", "second", "unconfigured"):
        (tmp_path / "plugins/cache" / market / "a.b/local").mkdir(parents=True)
    source = PluginStoreSource(
        tmp_path,
        LocalConfigState(
            layers=(
                ConfigLayer(
                    tmp_path / "config.toml",
                    "user",
                    contents=(
                        '[plugins."a.b@first"]\nenabled=true\n'
                        '[plugins."a.b@second"]\nenabled=true\n'
                        '[plugins."missing@first"]\nenabled=true\n'
                    ),
                ),
                ConfigLayer(
                    tmp_path / "project/config.toml",
                    "project",
                    contents=('[plugins."a.b@first"]\nenabled=false\n'),
                ),
                ConfigLayer(
                    tmp_path / "ignored/config.toml",
                    "project",
                    "untrusted",
                    contents=('[plugins."a.b@second"]\nenabled=false\n'),
                ),
            )
        ),
    )
    assert [(i.identity, i.enabled, i.version, i.error) for i in source.installations()] == [
        ("a.b@first", False, "local", None),
        ("a.b@second", True, "local", None),
        ("missing@first", True, None, "plugin is not installed"),
    ]
