"""Configuration parsing for local and remote MCP environment references."""

import pytest

from corki.config import CorkiSettings, MCPEnvVar, MCPServerSettings


@pytest.mark.parametrize(
    "value",
    [
        True,
        "ONE",
        {},
        [1],
        [None],
        [{"name": 1}],
        [{"source": "local"}],
        [{"name": "X", "extra": "field"}],
        [{"name": "X", "source": True}],
        [{"name": "X", "source": "LOCAL"}],
    ],
)
def test_env_reference_configuration_rejects_malformed_values(value):
    with pytest.raises(ValueError, match="env_vars"):
        MCPServerSettings.from_mapping("docs", {"command": "fixture", "env_vars": value})


@pytest.mark.parametrize("source", [None, "local", "remote"])
def test_structured_references_are_immutable_and_keep_exact_names(source):
    raw = {"name": " NAME ", "source": source}
    values = ["PLAIN", raw]
    settings = MCPServerSettings("docs", "stdio", command="fixture", env_vars=values)
    raw["name"] = "CHANGED"
    values.append("LATE")
    assert settings.env_vars == ("PLAIN", MCPEnvVar(" NAME ", source))


@pytest.mark.parametrize("value", [[], ["NAME"]])
def test_http_rejects_explicit_stdio_references_even_when_empty(value):
    with pytest.raises(ValueError, match="only supported for stdio"):
        MCPServerSettings.from_mapping(
            "docs", {"transport": "http", "url": "https://fixture.test", "env_vars": value}
        )


def test_toml_preserves_mixed_string_and_structured_references(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        '[mcp.servers.docs]\ncommand="fixture"\nenv_vars=["ONE",{name="TWO",source="local"}]\n'
    )
    (server,) = CorkiSettings.for_directory(tmp_path, config_file=config).mcp_servers
    assert server.env_vars == ("ONE", MCPEnvVar("TWO", "local"))


@pytest.mark.parametrize("transport", ["stdio", "http"])
def test_null_references_are_absent(transport):
    config = {"transport": transport, "env_vars": None}
    config.update(
        {"command": "fixture"} if transport == "stdio" else {"url": "https://fixture.test"}
    )
    assert MCPServerSettings.from_mapping("docs", config).env_vars == ()
