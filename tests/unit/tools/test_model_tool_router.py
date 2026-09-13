"""Model selectors and isolated candidate plans over captured host metadata."""

from dataclasses import replace

import pytest

from corki.code_mode.service import CodeModeService
from corki.config import CorkiSettings
from corki.config.model_context import parse_model_contexts
from corki.core.model_settings import capture_model_settings
from corki.protocol.context import ModelContextInfo
from corki.protocol.settings import ModelSettingsSnapshot
from corki.protocol.tools import ToolExposure, ToolSpec
from corki.tools import ToolRegistry
from corki.tools.discovery import build_tool_plan
from corki.tools.router import ToolRouterBuilder
from corki.tools.search_cache import ToolSearchHandlerCache


@pytest.mark.parametrize("search", [False, True])
@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_legacy_orchestrator_flag_cannot_filter_ordinary_mcp_identity(tmp_path, search, mode):
    class Remote:
        def __init__(self, name, server):
            self.spec = ToolSpec(name, "lookup", {}, exposure=ToolExposure.DEFERRED)
            self.mcp_server_name = server

    registry = ToolRegistry()
    app = Remote("innocent_name", "codex_apps")
    ordinary = Remote("mcp__codex_apps::ordinary", "ordinary")
    registry.register(app)
    registry.register(ordinary)
    search_owner, code_owner = registry.create_owner(), registry.create_owner()
    captured = registry.snapshot()
    builder = ToolRouterBuilder(
        registry, CodeModeService(registry), ToolSearchHandlerCache(), search_owner, code_owner
    )
    settings = CorkiSettings(tmp_path)
    permitted = builder.build(captured, settings, mode=mode, search=search)
    # Later handler mutation is not a new host registry publication.
    app.mcp_server_name, ordinary.mcp_server_name = "ordinary", "codex_apps"
    denied = builder.build(
        permitted, replace(settings, orchestrator_mcp_enabled=False), mode=mode, search=search
    )
    assert denied.get(app.spec.name) is app
    assert denied.get(ordinary.spec.name) is ordinary
    assert permitted.get(app.spec.name) is app
    assert captured.get(app.spec.name) is app
    restored = builder.build(denied, settings, mode=mode, search=search)
    assert restored.get(app.spec.name) is app
    if search:
        assert {s.name for s in denied.get("tool_search")._definitions} == {
            app.spec.name,
            ordinary.spec.name,
        }


@pytest.mark.parametrize("value", [None, "direct", "code_mode", "code_mode_only", "future", ""])
def test_catalog_selector_and_durable_snapshot_round_trip(tmp_path, value):
    info = parse_model_contexts({"fixture": {"tool_mode": value}})[0]
    expected = value if value in {"direct", "code_mode", "code_mode_only"} else None
    assert info.tool_mode == expected
    settings = CorkiSettings(tmp_path, model="fixture", model_contexts=(info,))
    snapshot = capture_model_settings(settings)
    assert ModelSettingsSnapshot.from_payload(snapshot.to_payload()) == snapshot
    legacy = snapshot.to_payload()
    legacy["model_info"].pop("tool_mode")
    assert ModelSettingsSnapshot.from_payload(legacy).model_info == replace(
        snapshot.model_info, tool_mode=None
    )


@pytest.mark.parametrize("value", [False, 1, [], {}])
def test_catalog_rejects_non_string_selector(value):
    with pytest.raises(ValueError, match="tool_mode"):
        parse_model_contexts({"fixture": {"tool_mode": value}})


def test_candidate_mask_and_search_do_not_mutate_selected_generation(tmp_path, monkeypatch):
    monkeypatch.setattr(CodeModeService, "available", staticmethod(lambda: True))

    class Remote:
        spec = ToolSpec("mcp__host::echo", "proof", {}, exposure=ToolExposure.DEFERRED)
        mcp_server_name = "host"
        mcp_omit_tools_from = ("code_mode",)

    registry = ToolRegistry()
    remote = Remote()
    owner = registry.create_owner()
    search_owner, code_owner = registry.create_owner(), registry.create_owner()
    registry.replace_owned(owner, (remote,))
    registry.seal()
    captured = registry.snapshot()
    before = captured.specs()
    # Handler mutation cannot rewrite a captured host mask.
    remote.mcp_omit_tools_from = ("direct", "deferred", "code_mode")
    settings = CorkiSettings(tmp_path, tool_search_mode="compatible")
    service = CodeModeService(registry)
    builder = ToolRouterBuilder(
        registry, service, ToolSearchHandlerCache(), search_owner, code_owner
    )
    direct = ModelContextInfo("direct", tool_mode="direct")
    nested = ModelContextInfo("nested", tool_mode="code_mode_only")
    selected = builder.build(captured, settings, direct)
    selected_specs = selected.specs()
    selected_search = selected.get("tool_search")
    candidate = builder.build(captured, settings, nested)
    assert candidate.spec(remote.spec.name).exposure == ToolExposure.DIRECT_MODEL_ONLY
    assert candidate.get("tool_search") is None
    assert candidate.get("exec") is not None
    assert candidate.get(remote.spec.name) is remote
    assert selected.specs() == selected_specs and selected.get("tool_search") is selected_search
    assert selected.spec(remote.spec.name).exposure == ToolExposure.DEFERRED_MODEL_ONLY
    assert captured.specs() == before and registry.specs() == before
    assert captured.mcp_mask(remote.spec.name) == ("code_mode",)
    rebuilt = builder.build(captured, settings, direct)
    assert rebuilt.specs() == selected_specs and rebuilt.get("tool_search") is selected_search
    # Realtime input can request an old-model candidate from an already-selected
    # snapshot. Its ephemeral exec/wait must not become foreign host tools.
    nested_again = builder.build(candidate, settings, nested)
    assert nested_again.specs() == candidate.specs()
    direct_again = builder.build(candidate, settings, direct)
    assert direct_again.specs() == selected_specs
    assert {
        s.name
        for s in build_tool_plan(
            candidate.specs(), (), "compatible", tool_mode=candidate.tool_mode
        ).advertised
    } == {"mcp__host::echo", "exec", "wait"}
    # Invalid publication must not replace the healthy generation or its owner map.
    remote.mcp_omit_tools_from = ["direct"]
    with pytest.raises(ValueError, match="mask"):
        registry.replace_owned(owner, (remote,))
    assert registry.snapshot().mcp_mask(remote.spec.name) == ("code_mode",)
    assert registry.owned_names(owner) == frozenset({remote.spec.name})


def test_candidate_cannot_invent_handler_or_mutate_exported_schema():
    class Probe:
        spec = ToolSpec("probe", "proof", {"type": "object"})

    registry = ToolRegistry()
    registry.register(Probe())
    snapshot = registry.snapshot()
    with pytest.raises(ValueError, match="no registered handler"):
        snapshot.derive(specs=(ToolSpec("invented", "no owner", {}),))
    changed = replace(snapshot.spec("probe"), parameters={"type": "string"})
    candidate = snapshot.derive(specs=(changed,), tool_mode="direct")
    changed.parameters["type"] = "number"
    candidate.spec("probe").parameters["type"] = "boolean"
    assert candidate.spec("probe").parameters == {"type": "string"}
    assert snapshot.spec("probe").parameters == {"type": "object"}


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("control", ["tool_search", "tool_search::inspect", "exec", "wait"])
def test_control_precedence_is_local_to_selected_plan_and_restores_source(
    tmp_path, strict, control
):
    class Tool:
        def __init__(self, name, exposure=ToolExposure.DIRECT):
            self.spec = ToolSpec(name, "proof", {}, exposure=exposure)

    registry = ToolRegistry()
    source = Tool(control)
    registry.register(source)
    registry.register(Tool("probe", ToolExposure.DEFERRED))
    search_owner, code_owner = registry.create_owner(), registry.create_owner()
    registry.seal()
    captured = registry.snapshot()
    before = captured.specs()
    builder = ToolRouterBuilder(
        registry, CodeModeService(registry), ToolSearchHandlerCache(), search_owner, code_owner
    )
    settings = CorkiSettings(tmp_path, error_on_tool_collisions=strict)
    mode = "direct" if control.startswith("tool_search") else "code_mode_only"
    if strict:
        expected = control.replace("::", ".") if "::" in control else f"functions.{control}"
        with pytest.raises(ValueError, match=f"tool collision: {expected}"):
            builder.build(captured, settings, mode=mode, search=True)
    else:
        selected = builder.build(captured, settings, mode=mode, search=True)
        assert selected.get(control) is not source
        search = selected.get("tool_search")
        assert [s.name for s in search._definitions] == ["probe"]
        restored = builder.build(selected, settings, mode="direct", search=False)
        assert restored.get(control) is source
        assert selected.get(control) is not source
        assert builder.build(selected, settings, mode=mode, search=True).specs() == selected.specs()
    assert captured.specs() == before and registry.specs() == before
    assert registry.get(control) is source


@pytest.mark.parametrize("name", ["exec", "wait", "tool_search"])
def test_removed_code_controls_and_search_itself_cannot_enable_discovery(tmp_path, name):
    class Tool:
        spec = ToolSpec(name, "proof", {}, exposure=ToolExposure.DEFERRED)

    registry = ToolRegistry()
    source = Tool()
    registry.register(source)
    search_owner, code_owner = registry.create_owner(), registry.create_owner()
    builder = ToolRouterBuilder(
        registry, CodeModeService(registry), ToolSearchHandlerCache(), search_owner, code_owner
    )
    plan = builder.build(
        registry.snapshot(), CorkiSettings(tmp_path), mode="code_mode_only", search=True
    )
    assert plan.get("tool_search") is (source if name == "tool_search" else None)


@pytest.mark.parametrize("hidden", [False, True])
def test_strict_description_collision_includes_hidden_without_inventory_gate(tmp_path, hidden):
    class Tool:
        def __init__(self, name, description, exposure):
            self.spec = ToolSpec(
                f"shared::{name}", "proof", {}, namespace_description=description, exposure=exposure
            )

    registry = ToolRegistry()
    registry.register(Tool("a", "First", ToolExposure.DIRECT))
    registry.register(Tool("b", "Second", ToolExposure.HIDDEN if hidden else ToolExposure.DIRECT))
    search_owner, code_owner = registry.create_owner(), registry.create_owner()
    builder = ToolRouterBuilder(
        registry, CodeModeService(registry), ToolSearchHandlerCache(), search_owner, code_owner
    )
    with pytest.raises(ValueError, match="tool collision: shared"):
        builder.build(registry.snapshot(), CorkiSettings(tmp_path, error_on_tool_collisions=True))


@pytest.mark.parametrize("invalid", [None, 0, 1, "false", [], {}])
def test_collision_setting_requires_actual_bool(tmp_path, invalid):
    with pytest.raises(ValueError, match="error_on_tool_collisions"):
        CorkiSettings(tmp_path, error_on_tool_collisions=invalid)


@pytest.mark.parametrize(
    "description,conflict", [("\x1c", True), ("\x1f", True), ("\u2003", False), ("\n\t", False)]
)
def test_strict_description_uses_rust_whitespace(tmp_path, description, conflict):
    class Tool:
        def __init__(self, name, value):
            self.spec = ToolSpec("shared::" + name, "proof", {}, namespace_description=value)

    registry = ToolRegistry()
    registry.register(Tool("z", description))
    registry.register(Tool("a", "Second"))
    search_owner, code_owner = registry.create_owner(), registry.create_owner()
    builder = ToolRouterBuilder(
        registry, CodeModeService(registry), ToolSearchHandlerCache(), search_owner, code_owner
    )
    settings = CorkiSettings(tmp_path, error_on_tool_collisions=True)
    if conflict:
        with pytest.raises(ValueError, match="tool collision: shared"):
            builder.build(registry.snapshot(), settings)
    else:
        builder.build(registry.snapshot(), settings)
