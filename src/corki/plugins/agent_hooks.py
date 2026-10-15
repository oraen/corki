"""Legacy hook metadata admission, not hook execution or trust-policy approval."""

import sys

from corki.protocol.wire_json import NUMBER_KEY, WireObject, check_fields, materialize, object_pairs
from corki.protocol.wire_numbers import WireNumber

_EVENTS = (
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "PreCompact",
    "PostCompact",
    "SessionStart",
    "SessionEnd",
    "UserPromptSubmit",
    "SubagentStart",
    "SubagentStop",
    "Stop",
    "Interrupt",
)


def validate_hook_metadata(value: object) -> None:
    """Accept exactly a path/list, inline file/list, or deserializable Value fallback.

    Only admission is consumed here, not the selected hook representation. A valid
    Value fallback therefore suffices; otherwise try the typed file alternatives,
    whose ignored fields need not themselves be valid Value representations.
    """
    materialize(value)  # Content buffering still decodes every string and map key.
    try:
        materialize(value, preserve_pairs=False)
        return
    except ValueError as fallback_error:
        buffered = _content(value)
        try:
            _file(buffered)
            return
        except ValueError:
            pass
        if isinstance(buffered, list):
            try:
                for item in buffered:
                    _file(item)
                return
            except ValueError:
                pass
        raise ValueError("invalid overlay hooks metadata") from fallback_error


def _content(value):
    # Raw JSON -> untagged Content routes decimal/overflow numbers through a
    # private map. Such maps can also satisfy defaulted structs' ignored fields.
    if isinstance(value, WireNumber):
        token = value.token
        if len(token) <= 20 and not any(char in token for char in ".e"):
            integer = int(token)
            if -(2**63) <= integer < 2**64:
                return integer
        return WireObject([(NUMBER_KEY, token)])
    if isinstance(value, dict):
        return WireObject([(key, _content(item)) for key, item in object_pairs(value)])
    if isinstance(value, list):
        return [_content(item) for item in value]
    return value


def _struct(value, fields, *, deny_unknown=False):
    # Serde's defaulted structs also accept positional sequences. Missing tail
    # fields use defaults; extra elements cannot be silently discarded.
    if isinstance(value, list) and len(value) <= len(fields):
        value = dict(zip(fields, value, strict=False))
    check_fields(value, set(fields))
    if deny_unknown and value.keys() - set(fields):
        raise ValueError("unknown hook file field")
    return value


def _string(value, field, *, required=False):
    item = value.get(field)
    if not isinstance(item, str) and (required or item is not None):
        raise ValueError(f"invalid hook string field {field}")


def _uint(value, field, limit=2**64):
    item = value.get(field)
    if item is not None and not (type(item) is int and 0 <= item < limit):
        raise ValueError(f"invalid unsigned hook field {field}")


def _file(value):
    value = _struct(value, ("description", "hooks"), deny_unknown=True)
    _string(value, "description")
    events = _struct(value.get("hooks", {}), _EVENTS)
    for name in _EVENTS:
        groups = events.get(name, [])
        if not isinstance(groups, list):
            raise ValueError("hook event must contain matcher groups")
        for raw_group in groups:
            group = _struct(raw_group, ("matcher", "hooks"))
            _string(group, "matcher")
            handlers = group.get("hooks", [])
            if not isinstance(handlers, list):
                raise ValueError("matcher hooks must be an array")
            for handler in handlers:
                _handler(handler)


def _handler(value):
    check_fields(value, {"type"})
    kind = value.get("type")
    if kind in ("prompt", "agent"):
        return
    if kind not in ("command", "mcp_tool"):
        raise ValueError("invalid hook handler type")
    fields = ("type", "timeout", "statusMessage")
    if kind == "command":
        fields += (
            "command",
            "commandWindows",
            "command_windows",
            "async",
            "additionalContextLimit",
        )
    else:
        fields += ("server", "tool", "input")
    check_fields(value, set(fields))
    _uint(value, "timeout")
    _string(value, "statusMessage")
    if kind == "command":
        _string(value, "command", required=True)
        _string(value, "commandWindows")
        _string(value, "command_windows")
        if "commandWindows" in value and "command_windows" in value:
            raise ValueError("duplicate commandWindows field")
        if type(value.get("async", False)) is not bool:
            raise ValueError("hook async must be a boolean")
        _uint(value, "additionalContextLimit", 2 * (sys.maxsize + 1))
    else:
        _string(value, "server", required=True)
        _string(value, "tool", required=True)
        configured = value.get("input", {})
        if not isinstance(configured, dict):
            raise ValueError("MCP hook input must be a map")
        # Map<String,Value> preserves private-looking keys but decodes each
        # value. Decode every duplicate before last-key-wins TOML validation.
        inputs = {
            key: materialize(item, preserve_pairs=False) for key, item in object_pairs(configured)
        }
        _toml_input(inputs)


def _toml_input(value):
    # With arbitrary_precision, serde_json numbers serialize as a private
    # string-bearing struct, including huge numbers. Do not impose i64 bounds.
    if value is None:
        raise ValueError("MCP hook input cannot contain null")
    if isinstance(value, (dict, list)):
        for item in value.values() if isinstance(value, dict) else value:
            _toml_input(item)
