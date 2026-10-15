"""Native OAuth configuration semantics, independent of performing a login."""

import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.config.toml_edits import add_missing_mcp_servers


@pytest.mark.parametrize("mode", ["file", "auto", "keyring"])
def test_global_oauth_store_policy_is_preserved(tmp_path, mode):
    config = tmp_path / "config.toml"
    config.write_text(f'mcp_oauth_credentials_store = "{mode}"\n')
    assert (
        CorkiSettings.for_directory(tmp_path, config_file=config).mcp_oauth_credentials_store
        == mode
    )


@pytest.mark.parametrize("mode", [None, True, 1, [], "FILE", "unknown"])
def test_invalid_global_oauth_store_policy_is_rejected(tmp_path, mode):
    with pytest.raises(ValueError, match="mcp_oauth_credentials_store"):
        CorkiSettings(tmp_path, mcp_oauth_credentials_store=mode)


def parse(**values):
    return MCPServerSettings.from_mapping(
        "fixture", {"url": "https://fixture.invalid/mcp", **values}
    )


def test_oauth_configuration_survives_atomic_install_snapshot(tmp_path):
    result = add_missing_mcp_servers(
        tmp_path / "config.toml",
        {
            "fixture": {
                "url": "https://fixture.invalid/mcp",
                "scopes": [" read ", "", "read", "read"],
                "oauth_resource": "resource-as-configured",
                "oauth": {
                    "client_id": " client ",
                    "callback_url": "callback-as-configured",
                    "callback_port": 0,
                },
            }
        },
    )
    server = result.servers[0]
    assert getattr(server, "scopes", None) == (" read ", "", "read", "read")
    assert getattr(server, "oauth_resource", None) == "resource-as-configured"
    oauth = getattr(server, "oauth", None)
    assert oauth is not None, "OAuth configuration was silently discarded"
    assert oauth.client_id == " client "
    assert oauth.callback_url == "callback-as-configured"
    assert oauth.callback_port == 0, "zero is rejected at login, not configuration parsing"


def test_absent_and_empty_oauth_or_scopes_remain_distinct():
    assert getattr(parse(), "oauth", None) is None
    assert getattr(parse(oauth={}), "oauth", None) is not None
    assert getattr(parse(), "scopes", None) is None
    assert getattr(parse(scopes=[]), "scopes", None) == ()


@pytest.mark.parametrize(
    "values",
    [
        {"oauth": False},
        {"oauth": []},
        {"oauth": "client"},
        {"oauth": {"client_id": 1}},
        {"oauth": {"callback_url": False}},
        *({"oauth": {"callback_port": v}} for v in [-1, 65536, True, 1.0, "80"]),
        {"scopes": "read"},
        {"scopes": [1]},
        {"scopes": False},
        {"oauth_resource": 42},
    ],
)
def test_malformed_oauth_configuration_is_not_silently_ignored(values):
    with pytest.raises(ValueError):
        parse(**values)


@pytest.mark.parametrize("values", [{"oauth": {}}, {"oauth_resource": ""}])
def test_stdio_rejects_http_oauth_fields(values):
    with pytest.raises(ValueError, match="http"):
        MCPServerSettings.from_mapping("stdio", {"command": "fixture", **values})


def test_scopes_are_a_shared_field_even_for_stdio():
    server = MCPServerSettings.from_mapping("stdio", {"command": "fixture", "scopes": []})
    assert getattr(server, "scopes", None) == ()


def test_direct_settings_take_immutable_copies():
    from dataclasses import FrozenInstanceError

    from corki.config import MCPServerOAuthSettings

    scopes = ["write", "read"]
    oauth = {"client_id": "first", "callback_port": 65535}
    server = MCPServerSettings(
        "fixture", "http", url="https://fixture.invalid", scopes=scopes, oauth=oauth
    )
    scopes.append("later")
    oauth["client_id"] = "later"
    assert server.scopes == ("write", "read")
    assert server.oauth == MCPServerOAuthSettings(client_id="first", callback_port=65535)
    with pytest.raises(FrozenInstanceError):
        server.oauth.client_id = "changed"


def test_nullable_strings_and_empty_values_follow_native_options():
    from corki.config import MCPServerOAuthSettings

    assert (
        parse(oauth={"client_id": None, "callback_url": None, "callback_port": None}).oauth
        == MCPServerOAuthSettings()
    )
    assert parse(
        oauth={"client_id": "", "callback_url": ""}, oauth_resource=""
    ).oauth == MCPServerOAuthSettings(client_id="", callback_url="")


def test_oauth_only_configuration_changes_are_distinguishable():
    assert parse(oauth={"callback_port": 1111}) != parse(oauth={"callback_port": 2222})
    assert parse(scopes=[]) != parse()
    assert parse(oauth_resource="first") != parse(oauth_resource="second")


def test_bad_existing_oauth_configuration_prevents_any_install_write(tmp_path):
    path = tmp_path / "config.toml"
    contents = '[mcp.servers.old]\nurl="https://fixture.invalid"\nscopes="bad"\n'
    path.write_text(contents)
    with pytest.raises(ValueError, match="scopes"):
        add_missing_mcp_servers(path, {"new": {"command": "fixture"}})
    assert path.read_text() == contents
