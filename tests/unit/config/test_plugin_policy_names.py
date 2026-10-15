"""Opaque selected-plugin IDs may coincide with Corki's legacy selector keys."""

import pytest

from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.config.plugin_policies import plugin_policies
from corki.mcp.approval_persistence import policy_document


@pytest.mark.parametrize("name", ["directories", "disabled"])
@pytest.mark.parametrize("entrypoint", ["cold", "typed", "raw"])
def test_table_valued_reserved_names_are_package_policies(tmp_path, name, entrypoint):
    entry = {"enabled": False, "mcp_servers": {"docs": {"disabled_tools": ["write"]}}}
    contents = (
        f"[plugins.{name}]\nenabled=false\n"
        f'[plugins.{name}.mcp_servers.docs]\ndisabled_tools=["write"]\n'
    )
    config = tmp_path / "config.toml"
    config.write_text(contents)
    if entrypoint == "cold":
        settings = CorkiSettings.for_directory(tmp_path, config_file=config)
        assert settings.disabled_plugins == frozenset({name})
        assert settings.plugin_dirs == ()
    elif entrypoint == "typed":
        assert plugin_policies({"plugins": {name: entry}}) == {name: entry}
    else:
        state = LocalConfigState((ConfigLayer(config, "user", contents=contents),))
        assert policy_document(state)["plugins"] == {name: entry}
