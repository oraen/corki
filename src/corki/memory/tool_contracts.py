"""Advertised memory schemas and native handler-owned argument decoding."""

from collections.abc import Mapping

from corki.protocol.tools import ToolCall
from corki.protocol.wire_json import loads_wire, materialize

_WHITESPACE = (
    "\t\n\v\f\r \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006"
    "\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
)
_MAX_USIZE = 2**64 - 1


def _object(properties: dict, required: tuple[str, ...] = ()) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _mode_schema() -> dict:
    return {
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": [kind]},
                    **({"line_count": {"type": "integer"}} if kind == "all_within_lines" else {}),
                },
                "required": ["type", "line_count"] if kind == "all_within_lines" else ["type"],
            }
            for kind in ("any", "all_on_same_line", "all_within_lines")
        ]
    }


def input_schema(operation: str) -> dict:
    """Native serialized input subset, not a substitute for serde argument parsing."""
    page = {
        "path": {"type": "string"},
        "cursor": {"type": "string"},
        "max_results": {"type": "integer"},
    }
    if operation == "list":
        return _object(page)
    if operation == "read":
        return _object(
            {
                "path": {"type": "string"},
                "line_offset": {"type": "integer"},
                "max_lines": {"type": "integer"},
            },
            ("path",),
        )
    if operation == "search":
        return _object(
            {
                **page,
                "queries": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "match_mode": _mode_schema(),
                "context_lines": {"type": "integer"},
                "case_sensitive": {"type": "boolean"},
                "normalized": {"type": "boolean"},
            },
            ("queries",),
        )
    return _object(
        {
            "filename": {
                "type": "string",
                "description": (
                    "Name of the note file to create, in YYYY-MM-DDTHH-MM-SS-<slug>.md format. "
                    "The slug must use only lowercase ASCII letters, digits, and hyphens."
                ),
            },
            "note": {
                "type": "string",
                "description": "Verbatim Markdown note to append to the ad-hoc memory notes.",
            },
        },
        ("filename", "note"),
    )


def output_schema(operation: str) -> dict:
    """Internal JSON result description; deliberately absent from HTTP tool definitions."""
    if operation == "add_ad_hoc_note":
        return _object({})
    if operation == "read":
        fields = {
            "path": {"type": "string"},
            "start_line_number": {"type": "integer", "minimum": 0},
            "content": {"type": "string"},
            "truncated": {"type": "boolean"},
        }
        return _object(fields, tuple(fields))
    fields = {
        "path": {"type": ["string", "null"]},
        "next_cursor": {"type": ["string", "null"]},
        "truncated": {"type": "boolean"},
    }
    if operation == "list":
        fields["entries"] = {
            "type": "array",
            "items": _object(
                {
                    "path": {"type": "string"},
                    "entry_type": {"type": "string", "enum": ["file", "directory"]},
                },
                ("path", "entry_type"),
            ),
        }
        return _object(fields, ("entries", "truncated"))
    match = {
        "path": {"type": "string"},
        "match_line_number": {"type": "integer", "minimum": 0},
        "content_start_line_number": {"type": "integer", "minimum": 0},
        "content": {"type": "string"},
        "matched_queries": {"type": "array", "items": {"type": "string"}},
    }
    fields.update(
        queries={"type": "array", "items": {"type": "string"}},
        match_mode=_mode_schema(),
        matches={"type": "array", "items": _object(match, tuple(match))},
    )
    return _object(fields, ("queries", "match_mode", "matches", "truncated"))


def parse_arguments(call: ToolCall, operation: str) -> Mapping[str, object]:
    """Decode one native Value, then enforce typed fields without coercion or identity changes."""
    if call.raw_arguments or call.arguments is None or call.parse_error is not None:
        value = (
            materialize(loads_wire(call.raw_arguments), preserve_pairs=False)
            if call.raw_arguments.strip(_WHITESPACE)
            else {}
        )
    else:
        value = call.arguments
    schema = input_schema(operation)
    if not isinstance(value, Mapping):
        raise ValueError("memory tool arguments must be a JSON object")
    extra = set(value).difference(schema["properties"])
    missing = set(schema["required"]).difference(value)
    if extra or missing:
        raise ValueError(
            f"invalid memory fields: unknown={sorted(extra)}, missing={sorted(missing)}"
        )
    for key, item in value.items():
        if item is None and key not in schema["required"]:
            continue
        if key in {"line_offset", "max_lines", "context_lines", "max_results"}:
            _usize(item, key)
        elif key in {"case_sensitive", "normalized"}:
            if type(item) is not bool:
                raise ValueError(f"{key} must be a boolean")
        elif key == "queries":
            if not isinstance(item, list) or any(not isinstance(query, str) for query in item):
                raise ValueError("queries must be an array of strings")
        elif key == "match_mode":
            if not isinstance(item, Mapping) or item.get("type") not in {
                "any",
                "all_on_same_line",
                "all_within_lines",
            }:
                raise ValueError("match_mode must be a tagged memory match-mode object")
            if item["type"] == "all_within_lines":
                _usize(item.get("line_count"), "match_mode.line_count")
        elif not isinstance(item, str):
            raise ValueError(f"{key} must be a string")
    return value


def _usize(value: object, field: str) -> None:
    if type(value) is not int or not 0 <= value <= _MAX_USIZE:
        raise ValueError(f"{field} must be an unsigned 64-bit integer")


def cursor_offset(value: str | None) -> int:
    """Rust usize cursor spelling accepts ASCII digits and an optional leading plus."""
    if value is None:
        return 0
    digits = value.removeprefix("+")
    if not digits or not digits.isascii() or not digits.isdecimal():
        raise ValueError(f"cursor '{value}' must be a non-negative integer")
    # Bound significant digits before conversion, without Python's decimal digit limit.
    significant = digits.lstrip("0") or "0"
    if len(significant) > 20 or int(significant) > _MAX_USIZE:
        raise ValueError(f"cursor '{value}' must be a non-negative integer")
    return int(significant)


def result_limit(value: int | None, maximum: int) -> int:
    """Apply native handler defaults and clamping after typed parsing."""
    return min(maximum, max(1, maximum if value is None else value))
