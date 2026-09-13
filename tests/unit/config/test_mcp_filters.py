import pytest

from corki.config import CorkiSettings, MCPServerSettings


@pytest.mark.parametrize("key", ["enabled_tools", "disabled_tools"])
@pytest.mark.parametrize("value", ["read", True, 5, {}, [1], ["read", None]])
def test_filter_configuration_rejects_non_string_arrays(key, value):
    with pytest.raises(ValueError, match=key):
        MCPServerSettings.from_mapping(
            "docs",
            {
                "transport": "http",
                "url": "https://fixture.test",
                key: value,
            },
        )


@pytest.mark.parametrize("value", [None, [], ["read", "read", " write ", ""]])
def test_filters_preserve_none_empty_and_exact_names_without_mutable_aliases(value):
    original = None if value is None else tuple(value)
    settings = MCPServerSettings("docs", "http", enabled_tools=value, disabled_tools=value)
    if value is not None:
        value.append("mutated")
    assert settings.enabled_tools == settings.disabled_tools == original


@pytest.mark.parametrize("allowlist", [None, [], ["read"]])
def test_toml_load_preserves_optional_allowlist(tmp_path, allowlist):
    config = tmp_path / "config.toml"
    entry = "" if allowlist is None else f"enabled_tools={allowlist!r}\n"
    config.write_text(
        '[mcp.servers.docs]\ntransport="http"\nurl="https://fixture.test"\n'
        + entry
        + 'disabled_tools=["write"]\n'
    )
    (server,) = CorkiSettings.for_directory(tmp_path, config_file=config).mcp_servers
    assert server.enabled_tools == (None if allowlist is None else tuple(allowlist))
    assert server.disabled_tools == ("write",)
