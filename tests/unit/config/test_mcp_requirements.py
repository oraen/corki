"""Native legacy identity and global/plugin requirement scoping semantics."""

from dataclasses import FrozenInstanceError, replace

import pytest

from corki.config import MCPServerSettings
from corki.config.mcp_requirements import MCPRequirements, MCPServerIdentity, MCPServerSource

URL = "https://allowed.invalid/mcp"
IDENTITY = {"identity": {"url": URL}}


@pytest.mark.parametrize(
    "requirements,config_allowed,plugin_allowed",
    [
        (None, True, True),
        ({}, True, True),
        ({"mcp_servers": {}}, False, False),
        ({"mcp_servers": {"docs": IDENTITY}}, True, True),
        ({"mcp_servers": {"other": IDENTITY}}, False, True),
        ({"plugins": {}}, True, True),
        ({"plugins": {"other": {}}}, True, True),
        ({"plugins": {"other": {"mcp_servers": {}}}}, True, False),
        ({"plugins": {"package": {"mcp_servers": {}}}}, True, False),
        ({"plugins": {"package": {"mcp_servers": {"docs": IDENTITY}}}}, True, True),
        (
            {"mcp_servers": {}, "plugins": {"package": {"mcp_servers": {"docs": IDENTITY}}}},
            False,
            False,
        ),
        ({"plugins": {"package": {}, "other": {"mcp_servers": {"docs": IDENTITY}}}}, True, False),
    ],
)
def test_none_empty_and_scoped_allowlists(requirements, config_allowed, plugin_allowed):
    policy = MCPRequirements.coerce(requirements)
    settings = MCPServerSettings("route", "http", url=URL)
    assert policy.allows(settings, MCPServerSource("docs")) == config_allowed
    assert policy.allows(settings, MCPServerSource("docs", "package")) == plugin_allowed


@pytest.mark.parametrize(
    "changes,allowed",
    [
        ({}, True),
        ({"args": ("arbitrary", "--flags")}, True),
        ({"command": "/usr/bin/server"}, False),
        ({"command": "Server"}, False),
        ({"transport": "http", "url": "server"}, False),
    ],
)
def test_legacy_command_is_exact_executable_and_ignores_args(changes, allowed):
    policy = MCPRequirements.from_mapping(
        {"mcp_servers": {"docs": {"identity": {"command": "server"}}}}
    )
    settings = replace(MCPServerSettings("docs", "stdio", command="server"), **changes)
    assert policy.allows(settings, MCPServerSource("docs")) == allowed


@pytest.mark.parametrize(
    "changes,allowed",
    [
        ({}, True),
        ({"headers": (("X-Extra", "yes"),), "http_headers_helper": "helper"}, True),
        ({"url": URL + "/"}, False),
        ({"url": URL.upper()}, False),
        ({"url": URL + "?extra=true"}, False),
        ({"transport": "stdio", "command": URL}, False),
    ],
)
def test_legacy_url_authorizes_complete_http_configuration_without_normalization(changes, allowed):
    policy = MCPRequirements.from_mapping({"mcp_servers": {"docs": IDENTITY}})
    settings = replace(MCPServerSettings("docs", "http", url=URL), **changes)
    assert policy.allows(settings, MCPServerSource("docs")) == allowed


def test_typed_policy_freezes_caller_containers():
    servers = [["docs", MCPServerIdentity("http", URL)]]
    packages = [["package", servers]]
    policy = MCPRequirements(servers, packages)
    servers.clear()
    packages.clear()
    assert policy.allows(
        MCPServerSettings("docs", "http", url=URL), MCPServerSource("docs", "package")
    )
    assert not policy.allows(MCPServerSettings("other", "http", url=URL), MCPServerSource("other"))
    with pytest.raises(FrozenInstanceError):
        policy.servers = None


@pytest.mark.parametrize(
    "identity",
    [
        {},
        {"url": 123},
        {"command": []},
    ],
)
def test_invalid_identity_never_becomes_unrestricted(identity):
    with pytest.raises(ValueError):
        MCPRequirements.from_mapping({"mcp_servers": {"docs": {"identity": identity}}})


@pytest.mark.parametrize(
    "identity,transport,value",
    [
        ({"command": "server", "url": URL}, "stdio", "server"),
        ({"command": "server", "unknown": "ignored"}, "stdio", "server"),
        ({"url": URL, "unknown": "ignored"}, "http", URL),
        ({"command": {"executable": "ignored", "args": []}, "url": URL}, "http", URL),
    ],
)
def test_legacy_untagged_identity_preserves_native_precedence(identity, transport, value):
    policy = MCPRequirements.from_mapping({"mcp_servers": {"docs": {"identity": identity}}})
    assert policy.servers == (("docs", MCPServerIdentity(transport, value)),)
