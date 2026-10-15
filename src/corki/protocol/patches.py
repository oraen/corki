"""Committed patch evidence for host events and the ledger, never model history."""

import json
from pathlib import Path

MAX_PATCH_RECORD_BYTES = 4_000_000


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate patch record field")
        value[key] = item
    return value


def decode_patch_record(payload: str) -> dict:
    if not isinstance(payload, str) or len(payload.encode()) > MAX_PATCH_RECORD_BYTES:
        raise ValueError("patch record exceeds transport limit or is not text")
    try:
        result = json.loads(payload, object_pairs_hook=_object)
    except (ValueError, RecursionError) as error:
        raise ValueError("invalid patch record JSON") from error
    if not isinstance(result, dict):
        raise ValueError("patch record must be an object")
    return result


def parse_patch_delta(payload: str) -> dict:
    delta = decode_patch_record(payload)
    if (
        set(delta) != {"version", "exact", "changes"}
        or type(delta["version"]) is not int
        or delta["version"] != 1
        or type(delta["exact"]) is not bool
        or not isinstance(delta["changes"], list)
    ):
        raise ValueError("invalid patch delta fields")
    for entry in delta["changes"]:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"path", "change"}
            or not _path(entry["path"])
            or not isinstance(entry["change"], dict)
        ):
            raise ValueError("invalid committed patch change")
        change = entry["change"]
        kind = change.get("kind")
        if kind == "add":
            valid = (
                set(change) == {"kind", "content", "overwritten_content"}
                and isinstance(change["content"], str)
                and _optional_text(change["overwritten_content"])
            )
        elif kind == "delete":
            valid = set(change) == {"kind", "content"} and isinstance(change["content"], str)
        elif kind == "update":
            valid = (
                set(change)
                == {"kind", "move_path", "old_content", "overwritten_move_content", "new_content"}
                and (change["move_path"] is None or _path(change["move_path"]))
                and isinstance(change["old_content"], str)
                and isinstance(change["new_content"], str)
                and _optional_text(change["overwritten_move_content"])
            )
        else:
            valid = False
        if not valid:
            raise ValueError("invalid committed patch content")
    return delta


def _path(value):
    return isinstance(value, str) and "\0" not in value and Path(value).is_absolute()


def _optional_text(value):
    return value is None or isinstance(value, str)
