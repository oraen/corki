from dataclasses import replace

import pytest

from corki.context.deferred_tools import (
    KEY,
    decode_snapshot,
    encode_snapshot,
    namespace_snapshot,
    render_namespaces,
)
from corki.context.world_state import changed_context_items, render_context_history
from corki.protocol.ids import new_turn_id
from corki.protocol.items import ContextItem, ContextRole
from corki.protocol.tools import ToolExposure, ToolSpec


def spec(name, description="", exposure=ToolExposure.DEFERRED):
    return ToolSpec(name, "searchable", {}, exposure=exposure, namespace_description=description)


def test_source_first_line_sorting_and_xml_golden():
    current = namespace_snapshot(
        (
            spec("hotline::read"),
            spec("gmail::read", "access your Google Gmail Account & labels"),
            spec("app::read", "  control the Codex App  \nAdditional instructions."),
        )
    )
    assert render_namespaces(current) == (
        "<tools>\nDeferred tool namespaces:\n- app: control the Codex App\n"
        "- gmail: access your Google Gmail Account &amp; labels\n- hotline\n</tools>\n"
    )
    assert render_namespaces({"<&>\"'": "<&>\"'"}) == (
        "<tools>\nDeferred tool namespaces:\n"
        "- &lt;&amp;&gt;&quot;&apos;: &lt;&amp;&gt;&quot;&apos;\n</tools>\n"
    )


def test_source_added_changed_removed_golden_and_empty_state():
    current = {"app": "control the Codex App", "gmail": "access your Google Gmail Account"}
    previous = {"gmail": "old Gmail description", "hotline": "access hotline information"}
    assert render_namespaces(current, previous) == (
        "<tools>\nAdded deferred tool namespaces:\n- app: control the Codex App\n"
        "- gmail: access your Google Gmail Account\nRemoved deferred tool namespaces:\n"
        "- hotline: access hotline information\n</tools>\n"
    )
    assert render_namespaces({}, {"app": "old"}) == (
        "<tools>\nRemoved deferred tool namespaces:\n- app: old\n"
        "No deferred tool namespaces remain.\n</tools>\n"
    )
    assert render_namespaces(current, current) == ""
    assert render_namespaces({}) == render_namespaces({}, {}) == ""
    assert render_namespaces(current, {}) == render_namespaces(current)


def test_exposure_first_nonblank_description_and_rust_line_semantics():
    current = namespace_snapshot(
        (
            spec("vault::z", "later"),
            spec("vault::b", " \nfirst nonblank body"),
            spec("vault::a", "\t "),
            spec("direct::read", "direct", ToolExposure.DIRECT),
            spec("hidden::read", "hidden", ToolExposure.HIDDEN),
            spec("code::read", "code", ToolExposure.CODE_MODE_ONLY),
            spec("model::read", "model", ToolExposure.DEFERRED_MODEL_ONLY),
            ToolSpec(
                "ordinary", "default source", {}, exposure=ToolExposure.DEFERRED, source="MCP"
            ),
            spec("unicode::read", "\x1cfirst\u2028second\r\nthird"),
        )
    )
    assert current == {"model": "model", "unicode": "\x1cfirst\u2028second", "vault": "later"}


@pytest.mark.parametrize(
    "first,expected", [("First", "First"), ("\t ", "Second"), (" \nbody", ""), ("\x1c", "\x1c")]
)
def test_description_selection_follows_registration_order_not_leaf_sort(first, expected):
    assert namespace_snapshot((spec("shared::z", first), spec("shared::a", "Second"))) == {
        "shared": expected
    }


def test_character_and_post_escape_byte_budgets_do_not_truncate_snapshot():
    assert namespace_snapshot((spec("app::read", "🦀" * 251),)) == {"app": "🦀" * 250}
    current = {f"namespace_{i}": "&" * 250 for i in range(100)}
    rendered = render_namespaces(current)
    assert len(rendered.encode()) <= 4096
    assert " additional namespaces omitted.\n" in rendered
    assert decode_snapshot(encode_snapshot(current)) == current
    # An oversized entry must not prevent a later short entry from fitting.
    assert "- z: short\n" in render_namespaces({"a" * 5000: "large", "z": "short"})
    assert len(render_namespaces({}, current).encode()) <= 4096
    assert "No deferred tool namespaces remain." in render_namespaces({}, current)


@pytest.mark.parametrize(
    "state",
    [
        None,
        "bad json",
        "[]",
        '{"version":true}',
        '{"version":2,"namespaces":{}}',
        '{"version":1,"namespaces":{"a":3}}',
    ],
)
def test_unknown_prior_state_renders_full_catalog(state):
    turn = new_turn_id()
    previous = ContextItem(KEY, ContextRole.DEVELOPER, "legacy", turn, snapshot_state=state)
    current = ContextItem(
        KEY,
        ContextRole.DEVELOPER,
        render_namespaces({"app": "new"}),
        turn,
        snapshot_state=encode_snapshot({"app": "new"}),
    )
    (update,) = changed_context_items((previous,), (current,), turn)
    assert update.content == current.content
    assert "Removed" not in update.content


def test_omitted_changes_removal_and_legacy_projection_preserve_raw_history():
    turn = new_turn_id()
    initial = {f"namespace_{i:03}": "&" * 250 for i in range(100)}
    first = ContextItem(
        KEY,
        ContextRole.DEVELOPER,
        render_namespaces(initial),
        turn,
        snapshot_state=encode_snapshot(initial),
    )
    changed = {**initial, "namespace_099": "&" * 249 + "z"}
    current = replace(
        first, content=render_namespaces(changed), snapshot_state=encode_snapshot(changed)
    )
    assert current.content == first.content
    (delta,) = changed_context_items((first,), (current,), turn)
    assert "- namespace_099: " + "&amp;" * 249 + "z\n" in delta.content
    assert delta.snapshot_content == current.content
    assert changed_context_items((first, delta), (current,), turn) == ()
    (removal,) = changed_context_items((first, delta), (), turn)
    assert "No deferred tool namespaces remain." in removal.content
    assert removal.snapshot_content == "" and decode_snapshot(removal.snapshot_state) == {}
    assert changed_context_items((first, delta, removal), (), turn) == ()
    assert render_context_history((first, delta, removal))[-2:] == (delta, removal)
    assert first.snapshot_state == encode_snapshot(initial)
