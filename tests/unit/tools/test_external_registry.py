"""Host-owned source precedence with frozen, reversible publication generations."""

import asyncio
import weakref
from itertools import permutations

import pytest

from corki.code_mode.service import CodeModeService
from corki.config import CorkiSettings
from corki.plugins.api import PluginRegistrar
from corki.protocol.tool_exposure import ToolNamespacePolicy
from corki.protocol.tools import ToolSpec
from corki.tools import ToolRegistry, ToolSource
from corki.tools.router import ToolRouterBuilder
from corki.tools.search_cache import ToolSearchHandlerCache


class Tool:
    def __init__(self, name, description):
        self.spec = ToolSpec(name, description, {"type": "object"}, source="forged-core")


@pytest.mark.parametrize("sealed", [False, True])
@pytest.mark.parametrize("failure", [ValueError, asyncio.CancelledError])
def test_composition_restores_owners_policy_sealing_and_snapshots(sealed, failure):
    registry = ToolRegistry()
    owner = registry.create_owner(source=ToolSource.DYNAMIC)
    original = Tool("existing", "original")
    registry.replace_owned(owner, (original,))
    policy = registry.namespace_policy
    if sealed:
        registry.seal()
    before = registry.snapshot()
    error = failure()
    with pytest.raises(failure) as caught, registry.composition():
        if not sealed:
            registry.configure_namespace_policy(ToolNamespacePolicy(("functions",), ()))
            registry.register(Tool("temporary", "temporary"))
            registry.seal()
        registry.replace_owned(owner, (Tool("replacement", "replacement"),))
        assert registry.get("replacement") is not None
        raise error
    assert caught.value is error
    assert registry.snapshot().specs() == before.specs()
    assert registry.get("existing") is original
    assert registry.namespace_policy is policy
    assert registry.owned_names(owner) == frozenset({"existing"})
    if sealed:
        assert registry.snapshot() is before
        with pytest.raises(RuntimeError, match="sealed"):
            registry.register(Tool("later", "later"))
    else:
        registry.register(Tool("later", "later"))
        assert registry.get("later") is not None


@pytest.mark.parametrize("order", list(permutations(ToolSource)))
def test_source_priority_is_not_creation_or_publication_order_and_withdrawal_restores_fallback(
    order,
):
    registry = ToolRegistry()
    owners = {source: registry.create_owner(source=source) for source in order}
    tools = {source: Tool("shared", source.name) for source in order}
    for source in order:
        registry.replace_owned(owners[source], (tools[source],))
    registry.seal()
    old = registry.snapshot()
    assert registry.get("shared") is tools[ToolSource.MCP]
    assert old.first_collision == "shared"
    registry.replace_owned(owners[ToolSource.MCP], ())
    extension = registry.snapshot()
    assert extension.get("shared") is tools[ToolSource.EXTENSION]
    registry.replace_owned(owners[ToolSource.EXTENSION], ())
    assert registry.get("shared") is tools[ToolSource.DYNAMIC]
    assert registry.snapshot().first_collision is None
    assert old.get("shared") is tools[ToolSource.MCP]
    assert extension.get("shared") is tools[ToolSource.EXTENSION]


def test_late_trusted_registration_wins_without_stealing_external_owner():
    registry = ToolRegistry()
    owner = registry.create_owner(source=ToolSource.EXTENSION)
    external, core = Tool("same", "external"), Tool("same", "core")
    registry.replace_owned(owner, (external,))
    registry.register(core)
    assert registry.get("same") is core
    assert registry.owned_names(owner) == frozenset({"same"})
    assert not registry.selected_owned_names(owner)
    registry.unregister("same")
    assert registry.get("same") is external
    assert registry.snapshot().first_collision is None


@pytest.mark.parametrize("name", ["exec_command", "shell_command"])
@pytest.mark.parametrize("core_present", [False, True])
def test_reserved_external_terminal_names_are_skipped_even_without_core(name, core_present):
    registry = ToolRegistry()
    owner = registry.create_owner(source=ToolSource.DYNAMIC)
    core = Tool(name, "core")
    if core_present:
        registry.register(core)
    registry.replace_owned(owner, (Tool(name, "external"), Tool("healthy", "external")))
    assert registry.get(name) is (core if core_present else None)
    assert registry.get("healthy") is not None
    assert registry.snapshot().first_collision == (name if core_present else None)


def test_first_collision_follows_source_traversal_including_duplicate_entries():
    registry = ToolRegistry()
    registry.register(Tool("core", "core"))
    owner = registry.create_owner(source=ToolSource.MCP)
    first = Tool("duplicate", "first")
    registry.replace_owned(owner, (first, Tool("core", "second"), Tool("duplicate", "last")))
    assert registry.snapshot().first_collision == "core"
    assert registry.get("duplicate") is first
    registry.replace_owned(owner, (first, Tool("duplicate", "second"), Tool("core", "last")))
    assert registry.snapshot().first_collision == "duplicate"


def test_external_failed_capture_is_atomic_and_superseded_handlers_are_released():
    registry = ToolRegistry()
    owner = registry.create_owner(source=ToolSource.MCP)
    original = Tool("old", "old")
    reference = weakref.ref(original)
    registry.replace_owned(owner, (original,))
    registry.seal()
    snapshot = registry.snapshot()

    class Broken:
        @property
        def spec(self):
            raise ValueError("broken metadata")

    with pytest.raises(ValueError, match="broken metadata"):
        registry.replace_owned(owner, (Tool("new", "new"), Broken()))
    assert registry.snapshot() is snapshot
    assert registry.owned_names(owner) == frozenset({"old"})
    registry.replace_owned(owner, ())
    del original
    assert reference() is snapshot.get("old")
    del snapshot
    assert reference() is None


def test_snapshot_control_removal_uses_captured_owner_and_restores_shadowed_external(tmp_path):
    registry = ToolRegistry()
    control_owner, search_owner = registry.create_owner(), registry.create_owner()
    external_owner = registry.create_owner(source=ToolSource.DYNAMIC)
    external = Tool("exec", "source")
    registry.replace_owned(external_owner, (external,))
    registry.replace_owned(control_owner, (Tool("exec", "global control"),))
    registry.seal()
    captured = registry.snapshot()
    # Remove the current global owner after capturing the old generation.
    registry.replace_owned(control_owner, ())
    builder = ToolRouterBuilder(
        registry, CodeModeService(registry), ToolSearchHandlerCache(), search_owner, control_owner
    )
    settings = CorkiSettings(tmp_path)
    direct = builder.build(captured, settings, mode="direct", search=False)
    assert direct.get("exec") is external
    with pytest.raises(ValueError, match="tool collision: functions.exec"):
        builder.build(
            captured,
            CorkiSettings(tmp_path, error_on_tool_collisions=True),
            mode="code_mode_only",
            search=False,
        )
    assert captured.get("exec").spec.description == "global control"


@pytest.mark.parametrize("source", ["MCP", "mcp", 1, True, object()])
def test_source_priority_requires_explicit_host_enum(source):
    with pytest.raises(ValueError, match="host-selected"):
        ToolRegistry().create_owner(source=source)


@pytest.mark.parametrize("trusted", [False, True])
def test_plugin_owner_rollback_cannot_remove_other_sources(trusted):
    registry = ToolRegistry()
    name = "plugin__sample__echo"
    source = Tool(name, "survivor")
    if trusted:
        registry.register(source)
    else:
        owner = registry.create_owner(source=ToolSource.DYNAMIC)
        registry.replace_owned(owner, (source,))
    api = PluginRegistrar("sample", registry)
    api.register_tool(name="echo", description="plugin", parameters={}, handler=lambda *_: "plugin")
    assert (registry.get(name) is source) is trusted
    api.rollback()
    assert registry.get(name) is source
    assert registry.snapshot().first_collision is None


def test_shadowed_metadata_is_frozen_before_it_becomes_visible():
    registry = ToolRegistry()
    high, low = (
        registry.create_owner(source=source) for source in (ToolSource.MCP, ToolSource.DYNAMIC)
    )
    original = Tool("same", "captured")
    registry.replace_owned(high, (Tool("same", "winner"),))
    registry.replace_owned(low, (original,))
    registry.seal()
    original.spec.parameters["type"] = "string"
    registry.replace_owned(high, ())
    assert registry.spec("same").parameters == {"type": "object"}
