"""Canonical namespace contracts independent of provider transport."""

from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.core.checkpoint import checkpoint_serializer
from corki.models.base import ModelError
from corki.models.namespaces import group_tool_definitions, request_tool_aliases, response_tool_name
from corki.models.types import ModelRequest
from corki.protocol.tool_names import compatible_tool_name
from corki.protocol.tools import ToolSpec, tool_spec_from_payload, tool_spec_to_payload


def test_namespace_merge_order_empty_description_fill_and_sorted_children():
    specs = (
        ToolSpec("notes::z", "z", {}, namespace_description=" "),
        ToolSpec("plain", "plain", {}),
        ToolSpec("notes::a", "a", {}, namespace_description="Notes"),
        ToolSpec("history::read", "read", {}),
    )
    output = group_tool_definitions(specs)
    assert [spec["name"] for spec in output] == [compatible_tool_name(spec.name) for spec in specs]
    assert all(spec["type"] == "function" and "tools" not in spec for spec in output)


def test_discovery_coalesces_ranked_outputs_without_resorting_or_replacing_first_description():
    specs = (
        ToolSpec("notes::z", "z", {}, namespace_description=" "),
        ToolSpec("notes::a", "a", {}, namespace_description="Notes"),
    )
    output = group_tool_definitions(specs)
    assert [s["name"] for s in output] == [compatible_tool_name(s.name) for s in specs]
    assert all("defer_loading" not in s for s in output)


@pytest.mark.parametrize("control", ["\x1c", "\x1d", "\x1e", "\x1f"])
def test_namespace_c0_description_is_not_rust_whitespace(control):
    specs = (
        ToolSpec("notes::z", "z", {}, namespace_description=control),
        ToolSpec("notes::a", "a", {}, namespace_description="Second"),
    )
    assert (
        group_tool_definitions(specs)[0]["description"]
        == specs[0].compatible_description()
    )


@pytest.mark.parametrize("mode", ["compatible", "native"])
def test_namespace_wire_aliases_do_not_enforce_strict_directory_policy(mode):
    request = ModelRequest(
        "fixture",
        "",
        (),
        (),
        (
            ToolSpec("notes::a", "a", {}, namespace_description="First"),
            ToolSpec("notes::b", "b", {}, namespace_description="Second"),
        ),
        tool_namespace_mode=mode,
    )
    assert len(request_tool_aliases(request)) == 2
    assert (
        group_tool_definitions(request.tools)[0]["description"]
        == request.tools[0].compatible_description()
    )


def test_compatibility_name_collision_is_not_silently_aliased():
    first = ToolSpec("notes::read", "read", {})
    second = ToolSpec(compatible_tool_name(first.name), "different tool", {})
    request = ModelRequest("fixture", "", (), (), (first, second))
    with pytest.raises(ModelError, match="collision"):
        request_tool_aliases(request)
    with pytest.raises(ModelError, match="collision"):
        request_tool_aliases(replace(request, tool_namespace_mode="native"))


def test_namespace_metadata_roundtrips_and_plain_legacy_payload_is_unchanged():
    spec = ToolSpec("notes::read", "read", {}, namespace_description="Notes")
    assert tool_spec_from_payload(tool_spec_to_payload(spec)) == spec
    serializer = checkpoint_serializer()
    assert serializer.loads_typed(serializer.dumps_typed(spec)) == spec
    plain = ToolSpec("read", "read", {})
    assert "namespace_description" not in tool_spec_to_payload(plain)
    assert plain.as_chat_completion_tool()["function"]["name"] == "read"


@pytest.mark.parametrize("namespace", [None, ""])
def test_default_namespace_alias_does_not_capture_named_namespaces(namespace):
    assert response_tool_name({"namespace": namespace, "name": "read"}) == "read"
    for named in ("notes", "functions"):
        with pytest.raises(ModelError, match="namespace"):
            response_tool_name({"namespace": named, "name": "read"})


@pytest.mark.parametrize("name", ["::read", "notes::", "functions::read", "a::b::c"])
def test_invalid_explicit_namespace_identity_is_rejected(name):
    with pytest.raises(ValueError, match="namespace"):
        ToolSpec(name, "read", {})


def test_namespace_mode_loads_from_toml_and_rejects_chat_native(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[provider]\napi_mode="responses"\n[tools]\nnamespace_mode="native"\n')
    assert (
        CorkiSettings.for_directory(tmp_path, config_file=config).tool_namespace_mode
        == "compatible"
    )
    assert (
        CorkiSettings(working_directory=tmp_path, tool_namespace_mode="native").tool_namespace_mode
        == "compatible"
    )


@pytest.mark.parametrize("invalid", [None, True, "all", []])
def test_invalid_namespace_mode_is_rejected(tmp_path, invalid):
    with pytest.raises(ValueError, match="namespace_mode"):
        CorkiSettings(working_directory=tmp_path, tool_namespace_mode=invalid)
    with pytest.raises(ValueError, match="tool_namespace_mode"):
        ModelRequest("fixture", "", (), (), (), tool_namespace_mode=invalid)


def test_code_mode_alias_collision_cannot_silently_hide_a_tool():
    from corki.code_mode.specs import nested_specs
    from corki.protocol.tool_exposure import ToolNamespacePolicy

    class Registry:
        namespace_policy = ToolNamespacePolicy()

        def specs(self):
            return (ToolSpec("notes::read", "named", {}), ToolSpec("notes__read", "plain", {}))

    with pytest.raises(ValueError, match="alias collision"):
        nested_specs(Registry())


@pytest.mark.parametrize("name", ["notes::read", "界::读取", "long_namespace::" + "x" * 100])
def test_tool_call_budget_reserves_the_larger_canonical_or_wire_name(name):
    from corki.context.tokens import estimate_item_tokens, estimate_text_tokens
    from corki.protocol.ids import TurnId, new_tool_call_id
    from corki.protocol.items import ToolCallItem, new_step_id
    from corki.protocol.tools import ToolCall

    call = ToolCall(new_tool_call_id(), name, {})
    item = ToolCallItem(call, TurnId("turn"), new_step_id())
    empty = replace(item, call=replace(call, name=""))
    assert estimate_item_tokens(item) - estimate_item_tokens(empty) == max(
        estimate_text_tokens(name), estimate_text_tokens(compatible_tool_name(name))
    )
