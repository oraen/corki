"""Native startup validates the full typed plugin policy before constructing Runtime."""

import pytest

from corki.config import CorkiSettings
from corki.config.plugin_policies import plugin_policies


def test_policy_mapping_is_detached_and_compatibility_selectors_are_not_packages():
    document = {
        "plugins": {
            "directories": ["local"],
            "disabled": ["legacy"],
            "fixture": {"enabled": False, "future_field": {"nested": ["kept"]}},
        }
    }
    parsed = plugin_policies(document)
    assert parsed == {"fixture": document["plugins"]["fixture"]}
    parsed["fixture"]["future_field"]["nested"].clear()
    assert document["plugins"]["fixture"]["future_field"]["nested"] == ["kept"]


@pytest.mark.parametrize(
    "document",
    [
        '[plugins.fixture]\nenabled="false"\n',
        "[plugins.fixture]\nenabled=0\n",
        "[plugins.fixture]\nmcp_servers=[]\n",
        '[plugins.fixture.mcp_servers.docs]\nenabled="false"\n',
        '[plugins.fixture.mcp_servers.docs]\nenabled_tools="read"\n',
        "[plugins.fixture.mcp_servers.docs]\ndisabled_tools=[1]\n",
        '[plugins.fixture.mcp_servers.docs]\ndefault_tools_approval_mode="invalid"\n',
        "[plugins.fixture.mcp_servers.docs]\ntools=[]\n",
        '[plugins.fixture.mcp_servers.docs.tools.read]\napproval_mode="invalid"\n',
        "[plugins.fixture.mcp_servers.docs.tools.read]\noutput_token_limit=0\n",
        "[plugins.fixture.mcp_servers.docs.tools.read]\noutput_token_limit=-1\n",
        "[plugins.fixture.mcp_servers.docs.tools.read]\noutput_token_limit=true\n",
    ],
)
def test_invalid_typed_plugin_policy_fails_before_runtime_allocation(tmp_path, document):
    config = tmp_path / "config.toml"
    config.write_text(document)
    with pytest.raises(ValueError):
        CorkiSettings.for_directory(tmp_path, config_file=config)


@pytest.mark.parametrize(
    "document",
    [
        "[plugins.fixture]\nenabled=true\n",
        '[plugins.fixture]\nfuture_field="ignored"\n',
        "[plugins.fixture.mcp_servers.docs.tools.read]\noutput_token_limit=20\n",
    ],
)
def test_valid_and_unknown_plugin_fields_remain_usable(tmp_path, document):
    config = tmp_path / "config.toml"
    config.write_text(document)
    assert CorkiSettings.for_directory(tmp_path, config_file=config).plugins_enabled
