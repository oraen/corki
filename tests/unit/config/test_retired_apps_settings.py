"""Old official platform configuration cannot affect ordinary startup policy."""

import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.config.managed_mcp import MCPRequirementsLayer, compose_mcp_requirements
from corki.config.mcp_requirements import MCPServerSource


@pytest.mark.parametrize("field", ["apps", "apps_enabled"])
def test_removed_host_fields_cannot_reenable_apps(tmp_path, field):
    with pytest.raises(TypeError, match=field):
        CorkiSettings(tmp_path, **{field: True})


@pytest.mark.parametrize("low,high", [(False, True), (True, False), (False, False)])
def test_legacy_layers_cannot_replace_ordinary_authority(low, high):
    snapshot = compose_mcp_requirements(
        (
            MCPRequirementsLayer(
                "low", f"[apps.mail]\nenabled={str(low).lower()}\n[mcp_servers]\n"
            ),
            MCPRequirementsLayer("high", f"[apps.mail]\nenabled={str(high).lower()}\n"),
        )
    )
    assert snapshot.sources == (("mcp_servers", ("low",)),)
    assert not snapshot.policy.allows(
        MCPServerSettings("mail", "stdio", command="fixture"), MCPServerSource("mail")
    )


@pytest.mark.parametrize(
    "legacy", ["apps=3", '[apps.mail]\nenabled="ignored"', "[apps.mail]\nenabled=false"]
)
def test_local_apps_is_not_a_startup_policy(tmp_path, legacy):
    config = tmp_path / "config.toml"
    original = legacy + '\n[features]\napps="ignored"\n[mcp.servers.docs]\ncommand="fixture"\n'
    config.write_text(original)
    settings = CorkiSettings.for_directory(tmp_path, config_file=config)
    assert not hasattr(settings, "apps")
    assert not hasattr(settings, "apps_enabled")
    assert settings.mcp_servers[0].name == "docs"
    assert settings.mcp_servers[0].command == "fixture"
    assert config.read_text() == original


@pytest.mark.parametrize(
    "legacy", ["apps=3", '[apps.mail]\nenabled="ignored"', "[apps.mail]\nenabled=false"]
)
def test_managed_apps_is_not_an_authority(legacy):
    snapshot = compose_mcp_requirements(
        (
            MCPRequirementsLayer(
                "host", legacy + '\n[mcp_servers.docs.identity]\ncommand="fixture"\n'
            ),
        )
    )
    assert not hasattr(snapshot, "apps")
    assert snapshot.sources == (("mcp_servers", ("host",)),)
    assert snapshot.policy.allows(
        MCPServerSettings("docs", "stdio", command="fixture"), MCPServerSource("docs")
    )
    assert not snapshot.policy.allows(
        MCPServerSettings("other", "stdio", command="fixture"), MCPServerSource("other")
    )


@pytest.mark.parametrize("ordinary", ["mcp_servers=3", 'allowed_approval_policies="never"'])
def test_ignoring_apps_does_not_relax_managed_validation(ordinary):
    with pytest.raises(ValueError, match="host"):
        compose_mcp_requirements(
            (MCPRequirementsLayer("host", ordinary + "\n[apps.mail]\nenabled=false\n"),)
        )


def test_ignoring_apps_does_not_relax_local_validation(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[apps.mail]\nenabled=false\n[mcp]\napproval_policy="invalid"\n')
    with pytest.raises(ValueError, match="approval_policy"):
        CorkiSettings.for_directory(tmp_path, config_file=config)
