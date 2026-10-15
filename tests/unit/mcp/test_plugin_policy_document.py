"""Raw service policy admission is whole-map and distinct from typed cold startup."""

import tomllib

import pytest

from corki.config.layers import ConfigLayer, LocalConfigState
from corki.config.plugins import plugin_selection
from corki.mcp.approval_persistence import policy_document


@pytest.mark.parametrize(
    "invalid",
    [
        "plugins=[]\n",
        "[plugins]\nbad=7\n",
        '[plugins.bad]\nenabled="false"\n',
        "[plugins.bad]\nmcp_servers=[]\n",
        "[plugins.bad.mcp_servers]\ndocs=1\n",
        '[plugins.bad.mcp_servers.docs]\nenabled="false"\n',
        '[plugins.bad.mcp_servers.docs]\nenabled_tools="read"\n',
        "[plugins.bad.mcp_servers.docs]\ndisabled_tools=[1]\n",
        '[plugins.bad.mcp_servers.docs]\ndefault_tools_approval_mode="invalid"\n',
        "[plugins.bad.mcp_servers.docs]\ntools=[]\n",
        '[plugins.bad.mcp_servers.docs.tools.read]\napproval_mode="invalid"\n',
        "[plugins.bad.mcp_servers.docs.tools.read]\noutput_token_limit=0\n",
    ],
)
def test_invalid_map_does_not_discard_other_domains_or_rewrite_raw_layers(
    tmp_path, caplog, invalid
):
    content = invalid + (
        "[apps.fixture]\nenabled=false\n"
        '[mcp.servers.ordinary]\nurl="https://ordinary.invalid"\nenabled=false\n'
    )
    if not invalid.startswith("plugins=[]"):
        content += "[plugins.good]\nenabled=false\n"
    state = LocalConfigState((ConfigLayer(tmp_path / "config.toml", "user", contents=content),))
    document = policy_document(state)
    original = tomllib.loads(content)
    assert document == {"plugins": {}, "mcp": original["mcp"]}
    assert state.layers[0].contents == content
    assert plugin_selection(state) == ([], frozenset())
    assert "ignoring package policy mapping" in caplog.text
    with pytest.raises(ValueError):
        plugin_selection(state, strict=True)


@pytest.mark.parametrize("scenario", ["overridden", "untrusted", "effective_invalid"])
def test_only_effective_enabled_layers_determine_policy_validity(tmp_path, caplog, scenario):
    good = '[plugins.fixture.mcp_servers.docs.tools.read]\napproval_mode="prompt"\n'
    bad = "[plugins.fixture.mcp_servers.docs]\ntools=[]\n"
    lower, upper = (bad, good) if scenario == "overridden" else (good, bad)
    state = LocalConfigState(
        (
            ConfigLayer(tmp_path / "user.toml", "user", contents=lower),
            ConfigLayer(
                tmp_path / "project.toml",
                "project",
                contents=upper,
                disabled_reason="untrusted" if scenario == "untrusted" else None,
            ),
        )
    )
    parsed = policy_document(state)
    expected = {"plugins": {}} if scenario == "effective_invalid" else tomllib.loads(good)
    assert parsed == expected
    assert bool(caplog.records) is (scenario == "effective_invalid")


def test_valid_unknown_fields_survive_but_compatibility_selectors_are_not_policies(tmp_path):
    content = (
        '[plugins]\ndirectories=["custom"]\ndisabled=["legacy"]\n'
        '[plugins.fixture]\nfuture_field={nested=["kept"]}\n'
    )
    state = LocalConfigState((ConfigLayer(tmp_path / "config.toml", "user", contents=content),))
    parsed = policy_document(state)
    assert parsed == {"plugins": {"fixture": {"future_field": {"nested": ["kept"]}}}}
    parsed["plugins"]["fixture"]["future_field"]["nested"].clear()
    assert policy_document(state)["plugins"]["fixture"]["future_field"]["nested"] == ["kept"]
    assert plugin_selection(state) == ([str(tmp_path / "custom")], frozenset({"legacy"}))


@pytest.mark.parametrize("field", ["directories", "disabled"])
def test_compatibility_selector_types_remain_strict(tmp_path, field):
    state = LocalConfigState(
        (ConfigLayer(tmp_path / "config.toml", "user", contents=f"[plugins]\n{field}=0\n"),)
    )
    with pytest.raises(ValueError, match="directories/disabled"):
        plugin_selection(state)
