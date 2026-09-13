"""Exact namespaces, frozen policy generations and six-exposure semantics."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from corki.code_mode.specs import nested_specs
from corki.config import CorkiSettings
from corki.protocol.tool_exposure import CODE_MODE_EXPOSURES, ToolNamespacePolicy
from corki.protocol.tools import ToolExposure, ToolSpec
from corki.tools import ToolRegistry
from corki.tools.discovery import build_tool_plan
from corki.tools.search import ToolSearchTool
from corki.tools.search_cache import ToolSearchHandlerCache


@pytest.mark.parametrize("mode", ["direct", "code_mode", "code_mode_only"])
@pytest.mark.parametrize("policy_kind", ["none", "direct_only", "excluded", "both"])
def test_plan_search_and_nested_tools_share_policy(mode, policy_kind):
    registry = ToolRegistry()
    registry.configure_namespace_policy(
        ToolNamespacePolicy(
            ("group",) if policy_kind in {"direct_only", "both"} else (),
            ("group",) if policy_kind in {"excluded", "both"} else (),
        )
    )
    original = []
    for exposure in ToolExposure:
        spec = ToolSpec("group::" + exposure.value, "proof", {}, exposure=exposure)
        original.append(spec)
        registry.register(SimpleNamespace(spec=spec))
    registry.register(ToolSearchTool(registry))
    registry.seal()
    snapshot = registry.snapshot()
    effective = snapshot.specs()
    plan = build_tool_plan(effective, (), "native", tool_mode=mode)
    nested = nested_specs(snapshot)
    candidates = {name for name, _ in registry.deferred_entries()}
    for raw in original:
        exposure = (
            ToolExposure.DIRECT_MODEL_ONLY
            if policy_kind in {"direct_only", "both"} and raw.exposure in CODE_MODE_EXPOSURES
            else raw.exposure
        )
        assert registry.get(raw.name).spec == raw  # No handler mutation.
        assert registry.spec(raw.name) == snapshot.spec(raw.name) == replace(raw, exposure=exposure)
        assert (raw.name in candidates) == exposure.is_deferred
        assert (raw.name in {s.name for s in nested.values()}) == (
            exposure in CODE_MODE_EXPOSURES and policy_kind not in {"excluded", "both"}
        )
        direct = exposure.is_model_visible and (
            mode != "code_mode_only" or exposure == ToolExposure.DIRECT_MODEL_ONLY
        )
        assert (raw.name in {spec.name for spec in plan.advertised}) == direct


def test_exact_default_namespace_and_search_generation_are_not_source_labels():
    registry = ToolRegistry()
    owner = registry.create_owner()
    entries = tuple(
        SimpleNamespace(
            spec=ToolSpec(name, "proof", {}, exposure=ToolExposure.DEFERRED, source="notes")
        )
        for name in ("plain", "mcp__notes__lookup", "notes::lookup", "notes_extra::lookup")
    )
    registry.replace_owned(owner, entries)
    cache = ToolSearchHandlerCache()
    initial = cache.get_or_build(registry)
    old = registry.snapshot()
    selected = ["notes", "functions"]
    policy = ToolNamespacePolicy(selected, ["notes_extra"])
    registry.configure_namespace_policy(policy)
    selected.clear()
    assert policy.direct_only == ("notes", "functions")
    search = cache.get_or_build(registry)
    assert search is not initial
    assert [s.name for s in search._definitions] == ["notes_extra::lookup"]
    assert all(s.exposure == ToolExposure.DEFERRED for s in old.specs())
    registry.seal()
    before = registry.snapshot()
    registry.replace_owned(
        owner, tuple(SimpleNamespace(spec=replace(e.spec, description="new")) for e in entries)
    )
    assert before.spec("notes::lookup").description == "proof"
    assert registry.spec("notes::lookup").exposure == ToolExposure.DIRECT_MODEL_ONLY
    assert not nested_specs(registry)
    with pytest.raises(RuntimeError, match="sealed"):
        registry.configure_namespace_policy(ToolNamespacePolicy())
    assert [s.name for s in cache.get_or_build(registry)._definitions] == ["notes_extra::lookup"]


@pytest.mark.parametrize("field", ["direct_only_tool_namespaces", "excluded_tool_namespaces"])
@pytest.mark.parametrize("invalid", [None, True, "functions", {}, [1], [True]])
def test_invalid_namespace_arrays_fail_before_runtime(tmp_path, field, invalid):
    with pytest.raises(ValueError, match=field):
        CorkiSettings(working_directory=tmp_path, **{"code_mode_" + field: invalid})


@pytest.mark.parametrize(
    "text,mode",
    [
        ("[features]\ncode_mode=true\n", "code_mode"),
        ("[features]\ncode_mode=false\ncode_mode_only=true\n", "code_mode_only"),
        (
            '[features.code_mode]\nenabled=false\ndirect_only_tool_namespaces=["functions"]\n',
            "direct",
        ),
        ('[tools]\nmode="direct"\n[features.code_mode]\nenabled=true\n', "direct"),
    ],
)
def test_native_feature_config_and_explicit_compatibility_selector(tmp_path, text, mode):
    config = tmp_path / "config.toml"
    config.write_text(text)
    assert CorkiSettings.for_directory(tmp_path, config_file=config).tool_mode == mode


@pytest.mark.parametrize(
    "text",
    [
        '[features]\ncode_mode="bad"\n',
        '[features]\ncode_mode_only="bad"\n',
        "[features.code_mode]\nenabled=1\n",
        '[features.code_mode]\nexcluded_tool_namespaces="notes"\n',
        "[features.code_mode]\ndirect_only_tool_namespaces=[true]\n",
    ],
)
def test_invalid_native_feature_config_is_rejected(tmp_path, text):
    config = tmp_path / "config.toml"
    config.write_text(text)
    with pytest.raises(ValueError, match="features.code_mode"):
        CorkiSettings.for_directory(tmp_path, config_file=config)
