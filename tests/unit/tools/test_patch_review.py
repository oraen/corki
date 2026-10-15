import json

import pytest

from corki.execution.patch_review import parse_patch_review


def review():
    return {
        "version": 1,
        "requires_approval": True,
        "patch": "PATCH",
        "cwd": "/project",
        "files": ["/project/a"],
        "changes": [{"path": "/project/a", "change": {"kind": "add", "content": "text"}}],
    }


@pytest.mark.parametrize("defect", ["version", "requires_approval", "files", "changes", "cwd"])
def test_invalid_review_cannot_authorize_execution(defect):
    value = review()
    value[defect] = {
        "version": True,
        "requires_approval": 1,
        "files": ["/project/b"],
        "changes": [],
        "cwd": "relative",
    }[defect]
    with pytest.raises(ValueError):
        parse_patch_review(json.dumps(value))


def test_duplicate_review_fields_rejected():
    payload = json.dumps(review()).replace('"version": 1', '"version": 1,"version": 1')
    with pytest.raises(ValueError):
        parse_patch_review(payload)


def test_move_destination_may_also_be_another_operation_source():
    value = review()
    value["files"].append("/project/b")
    value["changes"][0]["change"] = {"kind": "update", "diff": "diff", "move_path": "/project/b"}
    value["changes"].append({"path": "/project/b", "change": {"kind": "delete", "content": "old"}})
    assert parse_patch_review(json.dumps(value)) == value
