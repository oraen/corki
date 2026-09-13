import pytest

from corki.context.model_transition import model_snapshot, transition_target
from corki.protocol.context import ModelContextInfo
from corki.protocol.ids import TurnId


@pytest.mark.parametrize("body_scope", [False, True])
@pytest.mark.parametrize("tokens", [8999, 9000, 9001, 9499, 9500])
def test_downshift_strict_auto_threshold_and_usable_limit(body_scope, tokens):
    previous = ModelContextInfo("large", 100000)
    current = ModelContextInfo("small", 10000)
    target = transition_target(
        (model_snapshot(previous, TurnId("old")),),
        current,
        lambda _: previous,
        active_tokens=tokens,
        usable_tokens=9500,
        auto_limit=9000,
        body_scope=body_scope,
    )
    expected = tokens >= 9500 or (not body_scope and tokens > 9000)
    assert (target is not None) is expected
    if expected:
        assert target == (previous, "model_downshift")


@pytest.mark.parametrize(
    "previous,current", [(None, "b"), ("a", None), ("a", "a"), ("", ""), ("a", "b"), ("", "b")]
)
def test_only_two_present_different_hashes_force_same_model_compaction(previous, current):
    old = ModelContextInfo("same", 100000, comp_hash=previous)
    new = ModelContextInfo("same", 100000, comp_hash=current)
    target = transition_target(
        (model_snapshot(old, TurnId("old")),),
        new,
        lambda _: old,
        active_tokens=0,
        usable_tokens=95000,
        auto_limit=90000,
        body_scope=True,
    )
    assert (target is not None) is (
        previous is not None and current is not None and previous != current
    )


@pytest.mark.parametrize(
    "old_name,old_window", [("small", 100000), ("large", 10000), ("large", None)]
)
def test_no_downshift_without_distinct_model_and_larger_known_old_window(old_name, old_window):
    previous = ModelContextInfo(old_name, old_window)
    target = transition_target(
        (model_snapshot(previous, TurnId("old")),),
        ModelContextInfo("small", 10000),
        lambda _: previous,
        active_tokens=9501,
        usable_tokens=9500,
        auto_limit=9000,
        body_scope=False,
    )
    assert target is None


def test_missing_trusted_snapshot_does_not_guess_previous_model():
    assert (
        transition_target(
            (),
            ModelContextInfo("small", 10000),
            lambda _: pytest.fail("must not guess"),
            active_tokens=20000,
            usable_tokens=9500,
            auto_limit=9000,
            body_scope=False,
        )
        is None
    )


def test_comp_hash_catalog_roundtrip_preserves_empty_and_rejects_nonstring():
    from corki.config.model_context import parse_model_contexts

    assert parse_model_contexts({"model": {"comp_hash": ""}})[0].comp_hash == ""
    assert parse_model_contexts({"model": {}})[0].comp_hash is None
    with pytest.raises(ValueError, match="comp_hash"):
        parse_model_contexts({"model": {"comp_hash": False}})
