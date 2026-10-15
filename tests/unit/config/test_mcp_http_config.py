import pytest

from corki.config import CorkiSettings, MCPServerSettings


@pytest.mark.parametrize("field", ["http_headers", "env_http_headers"])
@pytest.mark.parametrize("value", [True, [], "x", 1, {"x": 1}, {1: "x"}])
def test_header_maps_reject_invalid_wire_types(field, value):
    with pytest.raises(ValueError, match=field):
        MCPServerSettings.from_mapping("docs", {"url": "https://fixture.test", field: value})


@pytest.mark.parametrize(
    "field,value",
    [
        ("http_headers", {}),
        ("env_http_headers", {}),
        ("bearer_token_env_var", "TOKEN"),
        ("headers", {}),
        ("url", "https://fixture.test"),
    ],
)
def test_stdio_rejects_http_fields_including_explicit_empty_tables(field, value):
    with pytest.raises(ValueError, match=field):
        MCPServerSettings.from_mapping("docs", {"command": "fixture", field: value})


@pytest.mark.parametrize("field,value", [("args", []), ("env", {}), ("cwd", ""), ("env_vars", [])])
def test_http_rejects_stdio_fields_even_when_empty(field, value):
    with pytest.raises(ValueError, match=field):
        MCPServerSettings.from_mapping("docs", {"url": "https://fixture.test", field: value})


@pytest.mark.parametrize("value", [True, 1, [], {}])
def test_bearer_reference_requires_exact_string(value):
    with pytest.raises(ValueError, match="bearer_token_env_var"):
        MCPServerSettings.from_mapping(
            "docs", {"url": "https://fixture.test", "bearer_token_env_var": value}
        )


def test_raw_bearer_and_ambiguous_literal_aliases_are_rejected():
    for extra in ({"bearer_token": "secret"}, {"headers": {}, "http_headers": {}}):
        with pytest.raises(ValueError):
            MCPServerSettings.from_mapping("docs", {"url": "https://fixture.test", **extra})


def test_direct_header_configuration_copies_mutable_maps_and_distinguishes_empty():
    raw = {"X-Key": "VALUE"}
    settings = MCPServerSettings("docs", "http", http_headers={}, env_http_headers=raw)
    raw["X-Key"] = "CHANGED"
    assert settings.http_headers == () and settings.env_http_headers == (("X-Key", "VALUE"),)
    assert MCPServerSettings("docs", "http").http_headers is None


def test_toml_source_style_url_only_http_and_legacy_header_alias(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        '[mcp.servers.docs]\nurl="https://fixture.test"\n'
        'bearer_token_env_var="TOKEN"\nhttp_headers={X="literal"}\n'
        'env_http_headers={X="ENV"}\n'
    )
    (settings,) = CorkiSettings.for_directory(tmp_path, config_file=config).mcp_servers
    assert settings.transport == "http" and settings.bearer_token_env_var == "TOKEN"
    assert settings.http_headers == (("X", "literal"),) and settings.env_http_headers == (
        ("X", "ENV"),
    )
    legacy = MCPServerSettings.from_mapping(
        "legacy", {"url": "https://fixture.test", "headers": {"X": "legacy"}}
    )
    assert legacy.headers == (("X", "legacy"),) and legacy.http_headers is None
