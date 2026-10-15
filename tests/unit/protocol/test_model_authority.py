"""Native activation equality distinguishes authority, not arbitrary metadata."""

from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.core.checkpoint import checkpoint_serializer
from corki.core.model_settings import capture_model_settings
from corki.core.step_settings import check_model_authority
from corki.protocol.model_authority import ModelAuthority
from corki.protocol.settings import ModelSettingsSnapshot


def test_catalog_defaults_empty_records_and_computer_use_coverage():
    assert ModelAuthority.from_catalog({}) == ModelAuthority.from_catalog(
        {
            "model_messages": {"auto_review": {}, "guardian_v2": {"transcript": {}}},
        }
    )
    assert ModelAuthority.from_catalog(
        {"node_repl_auto_review_required": True}
    ).computer_use_review_required
    assert not ModelAuthority.from_catalog(
        {"node_repl_auto_review_required": True, "guardian": {}}
    ).computer_use_review_required
    unknown = ModelAuthority.from_catalog({"guardian": {"computer_use": "future"}})
    assert unknown.computer_use_review_required
    assert unknown == ModelAuthority.from_catalog({"guardian": {"computer_use": "another-future"}})


def test_parent_text_comparison_preserves_empty_and_rust_trim_end():
    default = ModelAuthority.from_catalog({})
    empty = ModelAuthority.from_catalog({"model_messages": {"auto_review": {"policy": ""}}})
    assert default.policy != empty.policy

    def template(text):
        return ModelAuthority.from_catalog(
            {"model_messages": {"auto_review": {"policy_template": text}}}
        )

    assert template("policy") == template("policy \n\u3000")
    assert template("policy") != template("policy\x1c")
    assert template("policy") != template(" policy")


@pytest.mark.parametrize("reviewer", [None, "fixed-reviewer"])
@pytest.mark.parametrize("coverage", [False, True])
def test_classifier_guard_is_conditional_but_not_masked_by_reviewer(tmp_path, reviewer, coverage):
    snapshot = capture_model_settings(CorkiSettings(tmp_path, model="gpt-5.6-sol"))
    authority = replace(
        ModelAuthority(), reviewer=reviewer, guardian="0" * 64 if coverage else None
    )
    initial = replace(
        snapshot, model_info=replace(snapshot.model_info, activation_authority=authority)
    )
    destination = replace(
        initial,
        model_info=replace(
            initial.model_info, activation_authority=replace(authority, guardian_v2="1" * 64)
        ),
    )
    if coverage:
        with pytest.raises(ValueError, match="guardian_v2"):
            check_model_authority(initial, initial, destination)
    else:
        check_model_authority(initial, initial, destination)


def test_unchanged_reviewer_masks_only_parent_fallback(tmp_path):
    snapshot = capture_model_settings(CorkiSettings(tmp_path, model="gpt-5.6-sol"))
    authority = replace(ModelAuthority(), reviewer="fixed")
    initial = replace(
        snapshot, model_info=replace(snapshot.model_info, activation_authority=authority)
    )
    other = replace(authority, policy="1" * 64, node_policy="2" * 64, policy_template="3" * 64)
    selected = replace(initial, model_info=replace(initial.model_info, activation_authority=other))
    check_model_authority(initial, initial, selected)


def test_legacy_missing_authority_is_not_reconstructed_during_restore(tmp_path):
    snapshot = capture_model_settings(CorkiSettings(tmp_path, model="gpt-5.6-sol"))
    payload = snapshot.to_payload()
    payload["model_info"].pop("activation_authority")
    legacy = ModelSettingsSnapshot.from_payload(payload)
    assert legacy.model_info.activation_authority is None
    serde = checkpoint_serializer()
    assert serde.loads_typed(serde.dumps_typed(legacy)) == legacy
    with pytest.raises(ValueError, match="not recorded"):
        check_model_authority(legacy, legacy, snapshot)


@pytest.mark.parametrize("index", [0, 1, 2])
@pytest.mark.parametrize("fallback", [True, None])
def test_any_retained_fallback_or_unknown_provenance_prevents_activation(tmp_path, index, fallback):
    snapshot = capture_model_settings(CorkiSettings(tmp_path, model="gpt-5.6-sol"))
    snapshots = [snapshot] * 3
    snapshots[index] = replace(
        snapshot, model_info=replace(snapshot.model_info, used_fallback_model_metadata=fallback)
    )
    with pytest.raises(ValueError, match="non-fallback"):
        check_model_authority(*snapshots)


@pytest.mark.parametrize(
    "fields",
    [
        {"guardian": {"shell": False}},
        {"model_messages": []},
        {"node_repl_auto_review_required": "false"},
        {"model_messages": {"auto_review": {"policy": 1}}},
        {"model_messages": {"guardian_v2": {"review_threshold_basis_points": 65536}}},
        {"model_messages": {"guardian_v2": {"max_tool_call_lag": True}}},
        {"model_messages": {"guardian_v2": {"transcript": {"sources": "all"}}}},
    ],
)
def test_malformed_catalog_authority_is_not_silently_coerced(fields):
    with pytest.raises(ValueError):
        ModelAuthority.from_catalog(fields)
