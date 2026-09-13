"""Durable group membership, legacy boundaries, and whole-message budget trimming."""

import asyncio
import base64
from dataclasses import replace

import pytest

from corki.context.message_groups import freeze_context_messages, needs_initial_context
from corki.context.tokens import estimate_item_tokens, estimate_request_tokens
from corki.context.window import _drop_oldest_pair, _retained_copy
from corki.core.checkpoint import checkpoint_serializer
from corki.protocol.context_messages import context_message_groups
from corki.protocol.items import (
    CompactionItem,
    ContextItem,
    ContextRole,
    UserMessageItem,
    item_from_payload,
    item_to_payload,
)
from corki.storage import SQLiteSessionRepository

# Immutable142-generated MsgPack: class exists, group membership does not.
OLD = (
    "x/QCk7Rjb3JraS5wcm90b2NvbC5pdGVtc6tDb250ZXh0SXRlbYqja2V5pmxlZ2FjeaRyb2xlxycA"
    "k7Rjb3JraS5wcm90b2NvbC5pdGVtc6tDb250ZXh0Um9sZaR1c2Vyp2NvbnRlbnSob2xkIGJvZHmn"
    "dHVybl9pZKR0dXJuomlko29sZKpjcmVhdGVkX2F0uTIwMjYtMDktMDhUMDA6MDA6MDArMDA6MDCw"
    "c25hcHNob3RfY29udGVudMCvc291cmNlX2lucHV0X2lkwK5zbmFwc2hvdF9zdGF0ZcCsY29udGVudF9r"
    "aW5krGxlZ2FjeS5jbGFzcw=="
)


def fragment(name, role=ContextRole.DEVELOPER, **kwargs):
    return ContextItem(name, role, name, "turn", id=name, content_kind="fixture." + name, **kwargs)


def test_initial_and_delta_boundaries_ignore_silent_records_without_role_sorting():
    items = (
        fragment("a"),
        fragment("u", ContextRole.USER),
        fragment("b"),
        ContextItem("silent", ContextRole.USER, "", "turn", snapshot_state="hidden"),
        fragment("c"),
        fragment("alone", separate_message=True),
        fragment("d"),
    )
    initial = freeze_context_messages(items, initial=True)
    delta = freeze_context_messages(items, initial=False)
    assert [[p.key for p in group] for group in context_message_groups(initial)] == [
        ["a", "b", "c", "d"],
        ["alone"],
        ["u"],
    ]
    assert [[p.key for p in group] for group in context_message_groups(delta)] == [
        ["a"],
        ["u"],
        ["b", "c"],
        ["alone"],
        ["d"],
    ]
    assert all(i.message_group_id is None for i in items)
    assert initial[-1].is_snapshot_only and initial[-1].message_group_id is None
    assert not needs_initial_context(initial)
    assert needs_initial_context((*initial, CompactionItem("summary", None, "new")))


def test_later_same_role_messages_never_extend_recorded_group_and_budget_counts_one_envelope():
    original = freeze_context_messages((fragment("a"), fragment("b")), initial=False)
    later = freeze_context_messages((fragment("c"), fragment("d")), initial=False)
    combined = (*original, *later)
    assert list(context_message_groups(combined)) == [original, later]
    assert (
        estimate_request_tokens("", combined, ())
        == sum(estimate_item_tokens(i) for i in combined) - 24
    )
    assert _drop_oldest_pair(combined) == later
    assert combined[:2] == original
    assert (
        freeze_context_messages(
            tuple(replace(i, id="new-" + i.id) for i in original), initial=True
        )[0].message_group_id
        == "new-a"
    )


@pytest.mark.parametrize(
    "case", ["missing", "index", "size", "role", "turn", "interleave", "repeat", "anchor"]
)
def test_corrupt_or_partial_membership_is_not_silently_regrouped(case):
    first, second = freeze_context_messages((fragment("a"), fragment("b")), initial=False)
    sequence = {
        "missing": (first,),
        "index": (second,),
        "size": (first, replace(second, message_group_size=3)),
        "role": (first, replace(second, role=ContextRole.USER)),
        "turn": (first, replace(second, turn_id="other")),
        "interleave": (first, UserMessageItem("input", "turn"), second),
        "repeat": (first, second, first, second),
        "anchor": (replace(first, id="different"), second),
    }[case]
    with pytest.raises(ValueError, match="context message group"):
        list(context_message_groups(sequence))


@pytest.mark.parametrize(
    "fields",
    [
        {"message_group_id": ""},
        {"message_group_id": False},
        {"message_group_size": 0},
        {"message_group_index": 1},
        {"message_group_size": True},
        {"message_group_id": "a", "message_group_index": -1},
        {"message_group_id": "a", "message_group_index": 1},
    ],
)
def test_invalid_group_fields_fail_at_durable_contract(fields):
    with pytest.raises(ValueError, match="group membership"):
        fragment("a", **fields)


def test_retained_input_copy_gets_new_singleton_identity_but_cannot_copy_half_a_group():
    singleton = freeze_context_messages((fragment("skill"),), initial=False)[0]
    retained = _retained_copy(singleton)
    assert retained.id != singleton.id and retained.message_group_id == retained.id
    assert list(context_message_groups((retained,))) == [(retained,)]
    first, _ = freeze_context_messages((fragment("a"), fragment("b")), initial=False)
    with pytest.raises(ValueError, match="one fragment"):
        _retained_copy(first)


def test_legacy_and_new_checkpoint_sqlite_membership_round_trip(tmp_path):
    async def scenario():
        serializer = checkpoint_serializer()
        old = serializer.loads_typed(("msgpack", base64.b64decode(OLD)))
        assert old.content_kind == "legacy.class" and old.message_group_id is None
        payload = item_to_payload(old)
        assert not any(k.startswith("message_group") for k in payload)
        assert "separate_message" not in payload
        assert list(context_message_groups((old, replace(old, id="legacy2")))) == [
            (old,),
            (replace(old, id="legacy2"),),
        ]
        group = freeze_context_messages((fragment("a"), fragment("b")), initial=False)
        assert tuple(serializer.loads_typed(serializer.dumps_typed(group))) == group
        assert tuple(item_from_payload("context", item_to_payload(i)) for i in group) == group
        repository = SQLiteSessionRepository(tmp_path / "s.db")
        try:
            await repository.create_thread("thread", tmp_path)
            await repository.append_items("thread", (old, *group))
            await repository.append_items("thread", (item_from_payload("context", payload), *group))
            assert await repository.load_items("thread") == (old, *group)
        finally:
            await repository.close()

    asyncio.run(scenario())
