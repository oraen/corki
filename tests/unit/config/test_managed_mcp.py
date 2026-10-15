"""Native requirements layers validate shape before merge and patterns after it."""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from corki.config import MCPServerSettings
from corki.config.managed_mcp import (
    MCPRequirementsLayer,
    compose_mcp_requirements,
    load_mcp_requirements,
)
from corki.config.mcp_requirements import MCPRequirements, MCPServerSource


def layer(source, contents):
    return MCPRequirementsLayer(source, contents)


def allowed(snapshot, name, command, args=(), plugin=None):
    return snapshot.policy.allows(
        MCPServerSettings(name, "stdio", command=command, args=args),
        MCPServerSource(name, plugin),
    )


def test_regular_table_merge_keeps_low_rules_and_overrides_shared_scalar():
    snapshot = compose_mcp_requirements(
        (
            layer(
                "low",
                '[mcp_servers.shared.identity]\ncommand="low"\n'
                '[mcp_servers.low.identity]\ncommand="low-only"',
            ),
            layer("high", '[mcp_servers.shared.identity]\ncommand="high"'),
        )
    )
    assert allowed(snapshot, "shared", "high")
    assert not allowed(snapshot, "shared", "low")
    assert allowed(snapshot, "low", "low-only")
    assert not allowed(snapshot, "extra", "high")
    assert snapshot.sources == (("mcp_servers", ("high", "low")),)


def test_high_empty_table_does_not_erase_lower_allowlist():
    snapshot = compose_mcp_requirements(
        (
            layer("low", '[mcp_servers.docs.identity]\ncommand="server"'),
            layer("high", "[mcp_servers]"),
        )
    )
    assert allowed(snapshot, "docs", "server")
    assert not allowed(snapshot, "other", "server")


def test_array_replacement_compiles_only_the_effective_regex():
    snapshot = compose_mcp_requirements(
        (
            layer(
                "low",
                '[mcp_servers.docs.identity.command]\nexecutable="server"\n'
                'args=[{match="regex", expression="["}]',
            ),
            layer(
                "high",
                '[mcp_servers.docs.identity.command]\nexecutable="server"\n'
                'args=[{match="exact", value="ok"}, {match="prefix", value="--"}]',
            ),
        )
    )
    assert allowed(snapshot, "docs", "server", ("ok", "--yes"))
    assert not allowed(snapshot, "docs", "server", ("ok",))


@pytest.mark.parametrize(
    "contents",
    [
        '[mcp_servers.docs.identity.command]\nexecutable="server"',
        "[mcp_servers.docs.identity.command]\nexecutable=3\nargs=[]",
        '[mcp_servers.docs.identity.url]\nmatch="regex"\nexpression=3',
        '[mcp_servers.docs.identity.url]\nmatch="exact"\nvalue="x"\nunknown=true',
        '[mcp_servers.docs.identity]\ncommand={executable="x",args=[]}\nextra=true',
        "mcp_servers=3",
        'allowed_approval_policies="never"',
        "[plugins.package]\nallowed=true",
    ],
)
def test_invalid_layer_cannot_be_repaired_by_later_layer(contents):
    with pytest.raises(ValueError, match="bad-low"):
        compose_mcp_requirements(
            (
                layer("bad-low", contents),
                layer("valid-high", '[mcp_servers.docs.identity]\ncommand="server"'),
            )
        )


@pytest.mark.parametrize("prefix", ["mcp_servers", "plugins.package.mcp_servers"])
def test_effective_regex_failure_names_source_and_server(prefix):
    with pytest.raises(ValueError) as error:
        compose_mcp_requirements(
            (layer("bad-pattern", f'[{prefix}.docs.identity.url]\nmatch="regex"\nexpression="["'),)
        )
    assert "bad-pattern" in str(error.value)
    assert "docs" in str(error.value)
    if prefix.startswith("plugins"):
        assert "package" in str(error.value)


def test_legacy_sibling_fields_are_retained_for_raw_merge():
    snapshot = compose_mcp_requirements(
        (
            layer("low", '[mcp_servers.docs.identity]\ncommand="legacy"\nurl="https://x"'),
            layer("high", '[mcp_servers.docs.identity]\ncommand={executable="x", args=[]}'),
        )
    )
    # The high command table replaces the low string, but the low URL survives:
    # native untagged legacy URL is attempted before typed command identity.
    assert snapshot.policy.allows(
        MCPServerSettings("docs", "http", url="https://x"), MCPServerSource("docs")
    )
    assert not allowed(snapshot, "docs", "x")


def test_sources_are_field_specific_high_first_deduplicated_and_immutable():
    layers = [
        layer("system", "[mcp_servers]"),
        layer("host", "[plugins.package.mcp_servers]"),
        layer("host", "[mcp_servers]"),
        layer("system", "[mcp_servers]"),
    ]
    snapshot = compose_mcp_requirements(layers)
    layers.clear()
    assert snapshot.sources == (
        ("mcp_servers", ("system", "host")),
        ("plugins", ("host",)),
    )
    assert not allowed(snapshot, "anything", "server", plugin="package")
    with pytest.raises(FrozenInstanceError):
        snapshot.policy = MCPRequirements()


def test_missing_file_is_absence_but_empty_mcp_table_denies(tmp_path):
    path = tmp_path / "requirements.toml"
    assert load_mcp_requirements(system_path=path).policy == MCPRequirements()
    path.write_text("[mcp_servers]", encoding="utf-8")
    snapshot = load_mcp_requirements(system_path=path)
    assert not allowed(snapshot, "docs", "server")
    assert snapshot.sources == (("mcp_servers", (str(path),)),)


@pytest.mark.parametrize("contents", [b"[broken", b"\xff", b"mcp_servers=7"])
def test_invalid_system_file_is_fatal_and_names_path(tmp_path, contents):
    path = tmp_path / "requirements.toml"
    path.write_bytes(contents)
    with pytest.raises(ValueError, match="requirements.toml"):
        load_mcp_requirements(system_path=path)


def test_read_error_is_not_absence(tmp_path, monkeypatch):
    path = tmp_path / "requirements.toml"

    def denied(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "read_text", denied)
    with pytest.raises(ValueError, match="requirements.toml"):
        load_mcp_requirements(system_path=path)


def test_system_layer_precedes_explicit_host_layers_and_snapshot_is_captured(tmp_path):
    path = tmp_path / "requirements.toml"
    path.write_text('[mcp_servers.docs.identity]\ncommand="low"', encoding="utf-8")
    snapshot = load_mcp_requirements(
        system_path=path,
        layers=(layer("host", '[mcp_servers.docs.identity]\ncommand="high"'),),
    )
    path.write_text("[mcp_servers]", encoding="utf-8")
    assert allowed(snapshot, "docs", "high")
    assert snapshot.sources == (("mcp_servers", ("host", str(path))),)


def test_relative_system_override_is_rejected():
    with pytest.raises(ValueError, match="absolute"):
        load_mcp_requirements(system_path=Path("requirements.toml"))


def test_final_merged_typed_object_is_validated_again():
    # Individually valid table variants can combine into an invalid tagged object.
    with pytest.raises(ValueError, match="effective MCP requirements"):
        compose_mcp_requirements(
            (
                layer("low", '[mcp_servers.docs.identity.url]\nmatch="regex"\nexpression="ok"'),
                layer("high", '[mcp_servers.docs.identity.url]\nmatch="exact"\nvalue="ok"'),
            )
        )


def test_scalar_replaces_invalid_regex_without_compiling_discarded_expression():
    snapshot = compose_mcp_requirements(
        (
            layer("low", '[mcp_servers.docs.identity.url]\nmatch="regex"\nexpression="["'),
            layer("high", '[mcp_servers.docs.identity]\nurl="https://allowed.invalid"'),
        )
    )
    assert snapshot.policy.allows(
        MCPServerSettings("docs", "http", url="https://allowed.invalid"), MCPServerSource("docs")
    )


def test_explicit_plugin_tables_merge_by_raw_package_and_server_names():
    snapshot = compose_mcp_requirements(
        (
            layer("low", '[plugins.package.mcp_servers.old.identity]\ncommand="old"'),
            layer("high", '[plugins.package.mcp_servers.new.identity]\ncommand="new"'),
        )
    )
    assert allowed(snapshot, "old", "old", plugin="package")
    assert allowed(snapshot, "new", "new", plugin="package")
    assert not allowed(snapshot, "new", "new", plugin="other")
    assert allowed(snapshot, "any", "any")


def test_directory_in_place_of_file_is_fatal(tmp_path):
    with pytest.raises(ValueError, match="Failed to read requirements file"):
        load_mcp_requirements(system_path=tmp_path)
