"""Previous-state recovery and producer ownership, separate from legacy prompts."""

import json
from dataclasses import replace

import pytest

from corki.context.extension_world_state import (
    PreviousKind,
    WorldStateFragment,
    WorldStateSection,
)
from corki.context.world_state import changed_context_items
from corki.protocol.items import ContextItem, ContextRole


@pytest.mark.parametrize("saved", [None, "broken", "null", "[]", '{"version":true,"active":true}'])
def test_corrupt_or_legacy_section_recovers_as_unknown(saved):
    seen = []
    section = WorldStateSection("fixture", "1", lambda previous: seen.append(previous))
    current = ContextItem(section.key, ContextRole.DEVELOPER, "", "turn")
    previous = replace(
        current, content="OLD", snapshot_state=saved, content_kind=section.key + ".instructions"
    )
    result = changed_context_items((previous,), (current,), "turn", world_state_sections=(section,))
    assert seen[0].kind is PreviousKind.UNKNOWN
    assert len(result) == 1 and result[0].is_snapshot_only


@pytest.mark.parametrize("retained", [False, True])
def test_retained_matcher_controls_reuse_of_exact_snapshot(retained):
    seen = []

    def render(previous):
        seen.append(previous)
        return WorldStateFragment(ContextRole.USER, "RESTORED")

    section = WorldStateSection(
        "fixture",
        "1",
        render,
        retained_matcher=lambda role, body: role is ContextRole.USER and body == "MATCH",
    )
    current = ContextItem(section.key, ContextRole.DEVELOPER, "", "turn")
    previous = replace(
        current, snapshot_state=section.state, content_kind=section.key + ".instructions"
    )
    fragment = ContextItem("other", ContextRole.USER, "MATCH" if retained else "OTHER", "turn")
    result = changed_context_items(
        (previous, fragment), (current, fragment), "turn", world_state_sections=(section,)
    )
    assert seen[0].kind is (PreviousKind.KNOWN if retained else PreviousKind.ABSENT)
    assert result[0].role is ContextRole.USER and result[0].content == "RESTORED"


def test_legacy_matcher_and_ordinary_prompt_removal_remain_distinct():
    seen = []
    section = WorldStateSection(
        "fixture",
        "1",
        lambda previous: seen.append(previous),
        legacy_matcher=lambda role, text: role is ContextRole.DEVELOPER and text == "LEGACY",
    )
    legacy = ContextItem("legacy.developer", ContextRole.DEVELOPER, "LEGACY", "turn")
    ordinary = ContextItem("host.ordinary", ContextRole.DEVELOPER, "INSTRUCTIONS", "turn")
    current = ContextItem(section.key, ContextRole.DEVELOPER, "", "turn")
    result = changed_context_items(
        (legacy, ordinary), (legacy, current), "turn", world_state_sections=(section,)
    )
    assert seen[0].kind is PreviousKind.UNKNOWN
    removal = next(row for row in result if row.key == ordinary.key)
    assert "no longer apply" in removal.content


@pytest.mark.parametrize("snapshot", ["null", "NaN", "Infinity", '{"x":NaN}'])
def test_invalid_current_snapshots_are_rejected(snapshot):
    with pytest.raises(ValueError):
        WorldStateSection("fixture", snapshot, lambda previous: None)


def test_legacy_custom_key_prefix_does_not_opt_into_producer_owned_removal():
    legacy = ContextItem("extension.world_state.old_host", ContextRole.DEVELOPER, "OLD", "turn")
    updates = changed_context_items((legacy,), (), "next")
    assert len(updates) == 1
    assert "no longer apply" in updates[0].content
    assert not updates[0].is_snapshot_only


def test_same_key_and_snapshot_shape_without_provenance_is_not_known_state():
    seen = []
    section = WorldStateSection("fixture", "1", lambda previous: seen.append(previous))
    current = ContextItem(section.key, ContextRole.DEVELOPER, "", "turn")
    legacy = replace(current, content="OLD_HOST", snapshot_state=section.state)
    result = changed_context_items((legacy,), (current,), "next", world_state_sections=(section,))
    assert seen[0].kind is PreviousKind.UNKNOWN
    assert len(result) == 1 and result[0].is_snapshot_only
    assert result[0].content_kind == section.key + ".instructions"


def test_comparison_snapshot_cleans_object_nulls_but_keeps_atomic_arrays():
    seen = []
    source = {
        "gone": None,
        "nested": {"gone": None, "keep": False},
        "array": [None, {"keep": None}],
    }
    expected = {"nested": {"keep": False}, "array": [None, {"keep": None}]}
    section = WorldStateSection("json", json.dumps(source), lambda previous: seen.append(previous))
    current = ContextItem(section.key, ContextRole.DEVELOPER, "", "turn")
    first = changed_context_items((), (current,), "turn", world_state_sections=(section,))
    assert json.loads(json.loads(first[0].snapshot_state)["snapshot"]) == expected
    assert json.loads(section.snapshot_json) == source
    assert changed_context_items(first, (current,), "next", world_state_sections=(section,)) == ()
    assert seen[-1].kind is PreviousKind.KNOWN
    assert json.loads(seen[-1].snapshot_json) == expected
