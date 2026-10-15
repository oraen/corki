"""Native search is catalog-owned; serialization preference is not provider authority."""

from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.config.model_context import parse_model_contexts
from corki.core.model_settings import capture_model_settings
from corki.protocol.context import ModelContextInfo
from corki.protocol.settings import ModelSettingsSnapshot
from corki.tools.router import search_enabled


@pytest.mark.parametrize("value", [None, 0, 1, "true", [], {}])
def test_search_capability_requires_boolean(value):
    with pytest.raises(ValueError, match="supports_search_tool"):
        parse_model_contexts({"fixture": {"supports_search_tool": value}})


@pytest.mark.parametrize("supported", [False, True])
def test_catalog_lookup_and_durable_search_capability(tmp_path, supported):
    info = parse_model_contexts({"fixture": {"supports_search_tool": supported}})[0]
    settings = CorkiSettings(tmp_path, model="vendor/fixture-version", model_contexts=(info,))
    snapshot = capture_model_settings(settings)
    assert snapshot.model_info.supports_search_tool is supported
    assert snapshot.model_info.used_fallback_model_metadata is False
    assert ModelSettingsSnapshot.from_payload(snapshot.to_payload()) == snapshot
    payload = snapshot.to_payload()
    payload["model_info"].pop("supports_search_tool")
    assert ModelSettingsSnapshot.from_payload(payload) == replace(
        snapshot, model_info=replace(snapshot.model_info, supports_search_tool=False)
    )
    assert not settings.model_context_info("missing").supports_search_tool
    assert not parse_model_contexts({"missing-field": {}})[0].supports_search_tool


@pytest.mark.parametrize("search", ["native", "compatible", "disabled"])
@pytest.mark.parametrize("supported", [False, True])
@pytest.mark.parametrize("namespace_tools", [False, True])
def test_provider_ceiling_and_compatible_path(tmp_path, search, supported, namespace_tools):
    settings = CorkiSettings(
        tmp_path,
        api_mode="responses",
        tool_search_mode=search,
        tool_namespace_mode="compatible",
        model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=supported),),
    )
    expected = search != "disabled"
    assert search_enabled(settings, namespace_tools=namespace_tools) is expected
    assert (
        search_enabled(
            replace(settings, tool_namespace_mode="native"), namespace_tools=namespace_tools
        )
        is expected
    )


@pytest.mark.parametrize(
    "model,mode",
    [("gpt-6-astra", "code_mode_only"), ("gpt-5.6-sol", "code_mode_only"), ("gpt-5.5", None)],
)
def test_bundled_lookup_inherits_native_tool_defaults(tmp_path, model, mode):
    settings = CorkiSettings(tmp_path, model="vendor/" + model + "-preview")
    snapshot = capture_model_settings(settings)
    assert snapshot.model_info.supports_search_tool
    assert snapshot.model_info.tool_mode == mode
    assert snapshot.model_info.used_fallback_model_metadata is False
    assert not settings.model_context_info("unlisted-model").supports_search_tool
