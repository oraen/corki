"""Untrusted compiler payloads cannot silently authorize retained stdin."""

from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.execution.terminal import parse_terminal_snapshot, unrestricted_terminal


@pytest.mark.parametrize("bad", [None, 1, "true", [], {}])
def test_invalid_feature_value_rejected_before_runtime(tmp_path, bad):
    with pytest.raises(ValueError, match="write_stdin_approval"):
        replace(CorkiSettings(working_directory=tmp_path), write_stdin_approval=bad)


@pytest.mark.parametrize(
    "defect", ["no-cap", "false-cap", "missing", "extra", "relative", "bypass-int", "profile-str"]
)
def test_snapshot_contract_is_strict(tmp_path, defect):
    raw = unrestricted_terminal(tmp_path).wire()
    value = {"terminal_review_supported": True, "terminal_snapshot": raw}
    if defect == "no-cap":
        value.pop("terminal_review_supported")
    elif defect == "false-cap":
        value["terminal_review_supported"] = 1
    elif defect == "missing":
        value.pop("terminal_snapshot")
    elif defect == "extra":
        raw["model_override"] = True
    elif defect == "relative":
        raw["policy_cwd"] = "relative"
    elif defect == "bypass-int":
        raw["bypassed"] = 1
    else:
        raw["profile"] = "disabled"
    with pytest.raises(ValueError):
        parse_terminal_snapshot(value)
