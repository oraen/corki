import json

import pytest

from corki.execution.patch_review import parse_patch_review


def review(root):
    return {
        "version": 1,
        "requires_approval": True,
        "patch": "PATCH",
        "cwd": str(root),
        "files": [str(root / "a")],
        "changes": [{"path": str(root / "a"), "change": {"kind": "add", "content": "text"}}],
    }


@pytest.mark.parametrize("defect", ["version", "requires_approval", "files", "changes", "cwd"])
def test_invalid_review_cannot_authorize_execution(tmp_path, defect):
    value = review(tmp_path)
    value[defect] = {
        "version": True,
        "requires_approval": 1,
        "files": [str(tmp_path / "b")],
        "changes": [],
        "cwd": "relative",
    }[defect]
    with pytest.raises(ValueError):
        parse_patch_review(json.dumps(value))


def test_duplicate_review_fields_rejected(tmp_path):
    payload = json.dumps(review(tmp_path)).replace('"version": 1', '"version": 1,"version": 1')
    with pytest.raises(ValueError):
        parse_patch_review(payload)


def test_move_destination_may_also_be_another_operation_source(tmp_path):
    value = review(tmp_path)
    destination = str(tmp_path / "b")
    value["files"].append(destination)
    value["changes"][0]["change"] = {"kind": "update", "diff": "diff", "move_path": destination}
    value["changes"].append({"path": destination, "change": {"kind": "delete", "content": "old"}})
    assert parse_patch_review(json.dumps(value)) == value
