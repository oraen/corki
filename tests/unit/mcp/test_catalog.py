"""Native source precedence, disabled winners and materialized-source semantics."""

from dataclasses import replace

import pytest

from corki.config import MCPServerSettings
from corki.config.mcp_requirements import MCPRequirements
from corki.mcp.catalog import MCPCatalog, MCPCatalogSource, MCPRegistration, MCPRemoval


def registration(kind="config", identity=None, order=0, *, enabled=True, name="docs"):
    return MCPRegistration(
        MCPServerSettings(name, "http", url=f"https://{identity or kind}.test", enabled=enabled),
        MCPCatalogSource(kind, identity, order),
    )


@pytest.mark.parametrize("reverse", [True, False])
def test_source_precedence_and_conflict_attribution(reverse):
    actions = [
        registration("plugin", "early", 0),
        registration("plugin", "late", 1),
        registration("selected_plugin", "selected"),
        registration(),
        registration("compatibility", "legacy"),
        registration("extension", "host"),
    ]
    catalog = MCPCatalog(tuple(reversed(actions) if reverse else actions))
    assert catalog.servers == (actions[-1],)
    assert len(catalog.conflicts) == 1
    assert catalog.conflicts[0].outcome == actions[-1]
    assert catalog.conflicts[0].contenders == (actions[1], actions[0])


@pytest.mark.parametrize("kind", ["plugin", "selected_plugin"])
def test_earlier_package_wins_even_when_inserted_first(kind):
    early, late = registration(kind, "early", 0), registration(kind, "late", 1)
    assert MCPCatalog((early, late)).servers == (early,)


@pytest.mark.parametrize("kind", ["plugin", "config", "selected_plugin"])
def test_disabled_winner_continues_except_selected_package(kind):
    disabled = registration(kind, None if kind == "config" else "package", enabled=False)
    overlay = registration("extension", "host")
    base = MCPCatalog((disabled,))
    winner = base.extend(overlay).servers[0]
    assert winner.source == overlay.source
    assert winner.settings.enabled is (kind == "selected_plugin")
    assert base.servers == (disabled,)


def test_disabled_loser_does_not_create_name_veto():
    disabled = registration("plugin", "package", enabled=False)
    host = registration("extension", "host")
    assert MCPCatalog((host, disabled)).servers == (host,)


@pytest.mark.parametrize("kind", ["plugin", "selected_plugin"])
@pytest.mark.parametrize("host_kind", ["config", "compatibility", "extension"])
def test_plugin_feature_projection_preserves_same_name_nonplugin_sources(kind, host_kind):
    plugin = registration(kind, "package", enabled=False)
    host = registration(host_kind, None if host_kind == "config" else "host")
    raw = MCPCatalog((plugin, host))
    projected = raw.without_plugins()
    assert projected.servers == (host,)
    assert projected.conflicts == ()
    assert raw.materialize((plugin.settings,)).servers[0].source == host.source
    plugin_only = MCPCatalog((plugin,))
    assert plugin_only.without_plugins().extend(host).servers == (host,)
    assert plugin_only.servers == (plugin,), "projection must not erase raw source knowledge"


@pytest.mark.parametrize("barrier", ["explicit", "extended", "managed"])
def test_plugin_projection_preserves_preexisting_name_vetoes(barrier):
    plugin = registration("plugin", "package", enabled=barrier != "extended")
    raw = MCPCatalog(
        (plugin,), disabled=frozenset({"docs"}) if barrier == "explicit" else frozenset()
    )
    if barrier == "extended":
        raw = raw.extend()
    elif barrier == "managed":
        raw = raw.constrain(MCPRequirements(servers=()))
    host = registration("extension", "host")
    assert raw.without_plugins().extend(host).servers == (
        replace(host, settings=replace(host.settings, enabled=False)),
    )


def test_plugin_projection_keeps_ordered_removals_and_host_conflicts():
    host = registration("extension", "host")
    removal = MCPRemoval("docs", MCPCatalogSource("extension", "remove", order=1))
    raw = MCPCatalog((registration("plugin", "package"), host, removal))
    projected = raw.without_plugins()
    assert projected.servers == ()
    assert projected.conflicts[0].contenders == (host, removal)
    restored = registration("extension", "restore", order=2)
    assert projected.extend(restored).servers == (restored,)


@pytest.mark.parametrize("barrier", ["none", "explicit", "extended", "managed"])
def test_policy_transforms_rederive_enablement_without_erasing_prior_vetoes(tmp_path, barrier):
    declaration = registration("plugin", "fixture", enabled=False)
    catalog = MCPCatalog(
        (declaration,), disabled=frozenset({"docs"}) if barrier == "explicit" else frozenset()
    )
    if barrier == "extended":
        catalog = catalog.extend(registration("extension", "host"))
    elif barrier == "managed":
        catalog = catalog.constrain(MCPRequirements(servers=()))
    catalog = catalog.with_default_cwd(tmp_path)
    transformed = catalog.transform_settings(lambda entry: replace(entry.settings, enabled=True))
    assert transformed.servers[0].settings.enabled is (barrier == "none")
    assert not catalog.servers[0].settings.enabled


@pytest.mark.parametrize("kind", ["compatibility", "extension"])
def test_equal_priority_last_action_wins_not_alphabetical_identity(kind):
    first, second = registration(kind, "z-first"), registration(kind, "a-second")
    catalog = MCPCatalog((first, second))
    assert catalog.servers == (second,)
    removal = MCPRemoval("docs", MCPCatalogSource(kind, "remove"))
    removed = catalog.extend(removal)
    assert removed.servers == ()
    assert removed.conflicts[0].outcome == removal
    assert removed.conflicts[0].contenders == (first, second, removal)


def test_extension_action_order_outranks_insertion_order():
    last, first = registration("extension", "last", 5), registration("extension", "first", 0)
    assert MCPCatalog((last, first)).servers == (last,)


def test_materialization_preserves_known_source_but_not_hidden_actions():
    known = registration("selected_plugin", "package", 4)
    catalog = MCPCatalog((registration("plugin", "loser"), known))
    changed = replace(known.settings, url="https://changed.test")
    unknown = replace(changed, name="new")
    result = catalog.materialize((changed, unknown))
    assert result.servers == (
        MCPRegistration(changed, replace(known.source, order=0)),
        MCPRegistration(unknown),
    )
    assert result.conflicts == ()


def test_input_and_returned_settings_mutations_cannot_modify_captured_catalog():
    args = ["trusted"]
    action = MCPRegistration(MCPServerSettings("docs", "stdio", command="server", args=args))
    catalog = MCPCatalog((action,))
    args.clear()
    action.settings.args.clear()
    catalog.servers[0].settings.args.clear()
    assert catalog.servers[0].settings.args == ["trusted"]


def test_controller_denial_in_base_cannot_be_reenabled_by_overlay():
    base = registration()
    overlay = registration("extension", "host")
    catalog = MCPCatalog((base,)).extend(overlay)
    denied = catalog.constrain(MCPRequirements(servers=()))
    assert denied.servers[0].source == overlay.source
    assert not denied.servers[0].settings.enabled
    # A trusted standalone host contribution is not user config or an attachment policy.
    host_only = MCPCatalog((overlay,)).constrain(MCPRequirements(servers=()))
    assert host_only.servers == (overlay,)


@pytest.mark.parametrize("value", ["false", 0, None, []])
def test_enabled_state_must_be_boolean(value):
    with pytest.raises(ValueError, match="enabled"):
        MCPServerSettings("docs", "http", enabled=value)
