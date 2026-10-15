"""Environment identity is an exact transport selector, not ignored metadata."""

from pathlib import Path

import pytest

from corki.config.features import MCPServerSettings
from corki.mcp.catalog import MCPCatalog, MCPRegistration
from corki.mcp.reconciliation import connection_identity


@pytest.mark.parametrize("environment_id", [None, "local", "remote", "", "local "])
@pytest.mark.parametrize("transport", ["stdio", "http"])
def test_parser_preserves_exact_environment_identity(environment_id, transport):
    fields = {"command": "server"} if transport == "stdio" else {"url": "https://fixture.invalid"}
    settings = MCPServerSettings.from_mapping("docs", {**fields, "environment_id": environment_id})
    assert settings.environment_id == ("local" if environment_id is None else environment_id)


@pytest.mark.parametrize("environment_id", [7, False, [], {}])
def test_environment_id_requires_a_string(environment_id):
    with pytest.raises(ValueError, match="environment_id"):
        MCPServerSettings.from_mapping(
            "docs", {"command": "server", "environment_id": environment_id}
        )


@pytest.mark.parametrize("transport", ["stdio", "http"])
def test_environment_id_participates_in_physical_connection_identity(transport):
    fields = {"command": "server"} if transport == "stdio" else {"url": "https://fixture.invalid"}
    local = MCPServerSettings.from_mapping("docs", fields)
    remote = MCPServerSettings.from_mapping("docs", {**fields, "environment_id": "remote"})
    assert connection_identity(local) != connection_identity(remote)


@pytest.mark.parametrize("cwd", [None, "~/remote-work", r"C:\Users\executor\work"])
def test_nonlocal_cwd_never_uses_controller_home_or_default_directory(tmp_path, cwd):
    fields = {"command": "server", "environment_id": "remote"}
    if cwd is not None:
        fields["cwd"] = cwd
    settings = MCPServerSettings.from_mapping("docs", fields)
    catalog = MCPCatalog((MCPRegistration(settings),)).with_default_cwd(tmp_path)
    actual = catalog.servers[0].settings
    assert actual.environment_id == "remote"
    assert actual.cwd == (None if cwd is None else Path(cwd))


def test_local_default_cwd_still_captured(tmp_path):
    settings = MCPServerSettings.from_mapping("docs", {"command": "server"})
    catalog = MCPCatalog((MCPRegistration(settings),)).with_default_cwd(tmp_path)
    assert catalog.servers[0].settings.cwd == tmp_path
