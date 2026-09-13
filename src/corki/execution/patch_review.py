"""Validate native patch review before displaying consent or starting writes."""

from pathlib import Path

from corki.execution.response import decode_helper_response


def parse_patch_review(payload: str) -> dict:
    review = decode_helper_response(
        b'{"ok":' + payload.encode() + b"}",
        expected=dict,
        error_prefix="invalid native patch review",
    )
    if (
        set(review) != {"version", "requires_approval", "patch", "cwd", "files", "changes"}
        or type(review["version"]) is not int
        or review["version"] != 1
        or type(review["requires_approval"]) is not bool
        or not isinstance(review["patch"], str)
        or not _absolute(review["cwd"])
        or not isinstance(review["files"], list)
        or not review["files"]
        or any(not _absolute(path) for path in review["files"])
        or len(set(review["files"])) != len(review["files"])
        or not isinstance(review["changes"], list)
        or not review["changes"]
    ):
        raise ValueError("invalid native patch review fields")
    paths = set()
    sources = set()
    for entry in review["changes"]:
        if not isinstance(entry, dict) or set(entry) != {"path", "change"}:
            raise ValueError("invalid native patch change")
        if not _absolute(entry["path"]) or entry["path"] in sources:
            raise ValueError("invalid native patch change path")
        sources.add(entry["path"])
        paths.add(entry["path"])
        change = entry["change"]
        if not isinstance(change, dict):
            raise ValueError("invalid native patch change detail")
        kind = change.get("kind")
        if not isinstance(kind, str):
            raise ValueError("invalid native patch change kind")
        if kind in {"add", "delete"}:
            valid = set(change) == {"kind", "content"} and isinstance(change["content"], str)
        elif kind == "update":
            valid = (
                set(change) == {"kind", "diff", "move_path"}
                and isinstance(change["diff"], str)
                and (change["move_path"] is None or _absolute(change["move_path"]))
            )
            if valid and change["move_path"] is not None:
                paths.add(change["move_path"])
        else:
            valid = False
        if not valid:
            raise ValueError("invalid native patch change detail")
    if paths != set(review["files"]):
        raise ValueError("native patch review paths do not match changes")
    return review


def _absolute(value):
    return isinstance(value, str) and Path(value).is_absolute()
