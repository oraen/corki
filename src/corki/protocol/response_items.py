"""Typed response-item validation/projection, following Codex ResponseItem serde.

Schema validation is separate from execution authorization: even discarded known
variants must decode. The returned projection omits unknown fields; callers may
retain their raw archive separately. No returned call is executed here.
"""

import math
from contextlib import suppress

from corki.protocol.wire_json import (
    check_fields,
    materialize,
    number_from_object,
    object_pairs,
    validate_buffered_value,
)
from corki.protocol.wire_numbers import WireNumber

METADATA = "internal_chat_message_metadata_passthrough"
_STRINGS = {
    "message": (("role",), ()),
    "agent_message": (("author", "recipient"), ()),
    "reasoning": ((), ()),
    "local_shell_call": ((), ()),
    "function_call": (("name", "arguments", "call_id"), ("namespace",)),
    "tool_search_call": (("execution",), ("status",)),
    "function_call_output": ((), ("call_id", "name", "namespace")),
    "custom_tool_call": (("call_id", "name", "input"), ("status", "namespace")),
    "custom_tool_call_output": (("call_id",), ("name",)),
    "tool_search_output": (("status", "execution"), ()),
    "web_search_call": ((), ("status",)),
    "image_generation_call": (("status", "result"), ("revised_prompt",)),
    "compaction": (("encrypted_content",), ()),
    "context_compaction": ((), ("encrypted_content",)),
}
_TEXT = {"input_text": "text", "output_text": "text"}
_MEDIA = {"input_image": "image_url", "input_audio": "audio_url"}
_ENCRYPTED = {"encrypted_content": "encrypted_content"}
_EXTRA = {
    "message": ("content", "phase"),
    "agent_message": ("content",),
    "reasoning": ("summary", "content", "encrypted_content"),
    "local_shell_call": ("call_id", "status", "action"),
    "function_call": ("encrypted_function_args",),
    "tool_search_call": ("call_id", "arguments"),
    "function_call_output": ("output",),
    "custom_tool_call_output": ("output",),
    "tool_search_output": ("call_id", "tools"),
    "web_search_call": ("action",),
}


def _object(value):
    if not isinstance(value, dict):
        raise ValueError("expected response object")
    return value


def _string(value):
    if not isinstance(value, str):
        raise ValueError("expected response string")
    return value


def _array(value):
    if not isinstance(value, list):
        raise ValueError("expected response array")
    return value


def _strings(value):
    return [_string(part) for part in _array(value)]


def _optional(source, target, field, validate=_string, *, emit_null=False):
    value = source.get(field)
    if value is not None:
        target[field] = validate(value)
    elif emit_null:
        target[field] = None


def _enum(value, choices):
    if not isinstance(value, str) or value not in choices:
        raise ValueError("invalid response enum value")
    return value


def _parts(value, variants):
    result = []
    for part in _array(value):
        _object(part)
        kind = _enum(part.get("type"), variants)
        field = variants[kind]
        check_fields(part, ("type", field, *(("detail",) if kind == "input_image" else ())))
        normalized = {"type": kind, field: _string(part.get(field))}
        if kind == "input_image":
            _optional(
                part, normalized, "detail", lambda v: _enum(v, ("auto", "low", "high", "original"))
            )
        result.append(normalized)
    return result


def _number(value):
    if isinstance(value, dict):
        return number_from_object(value)
    if isinstance(value, WireNumber):
        return value
    if type(value) not in (int, float) or (isinstance(value, float) and not math.isfinite(value)):
        raise ValueError("invalid metadata number")
    return value


def response_metadata_payload(value):
    """Decode external metadata, never accepting server-supplied host execution records."""
    source = _object(value)
    check_fields(source, ("turn_id", "create_time", "content_item_kinds"))
    result = {}
    _optional(source, result, "turn_id")
    _optional(source, result, "create_time", _number)
    # Codex serde_with::DefaultOnError discards the whole malformed vector.
    with suppress(ValueError):
        _optional(source, result, "content_item_kinds", _strings)
    # cell_id/executed_tool_calls/tool_calls_complete are host-owned and never decoded.
    return result


def _shell_action(value):
    source = _object(value)
    check_fields(source, ("type", "command", "timeout_ms", "working_directory", "user", "env"))
    result = {
        "type": _enum(source.get("type"), ("exec",)),
        "command": _strings(source.get("command")),
    }
    timeout = source.get("timeout_ms")
    if timeout is not None and (type(timeout) is not int or not 0 <= timeout <= 2**64 - 1):
        raise ValueError("invalid shell timeout_ms")
    result["timeout_ms"] = timeout
    for field in ("working_directory", "user"):
        _optional(source, result, field, emit_null=True)
    env = source.get("env")
    result["env"] = (
        None
        if env is None
        else {_string(key): _string(item) for key, item in object_pairs(_object(env))}
    )
    return result


def _web_action(value):
    source = _object(value)
    check_fields(source, ("type",))
    kind = _string(source.get("type"))
    fields = {"search": ("query",), "open_page": ("url",), "find_in_page": ("url", "pattern")}
    if kind not in fields:
        return {"type": "other"}
    check_fields(source, (*fields[kind], *(("queries",) if kind == "search" else ())))
    result = {"type": kind}
    for field in fields[kind]:
        _optional(source, result, field)
    if kind == "search":
        _optional(source, result, "queries", _strings)
    return result


def response_item_payload(value: dict) -> dict:
    """Validate a decoded item and project only fields its Rust variant serializes."""
    source = _object(value)
    check_fields(source, ("type",))
    kind = _string(source.get("type"))
    if kind == "compaction_summary":
        kind = "compaction"
    result = {"type": kind}
    if kind == "compaction_trigger":
        return result
    if kind == "configuration_update":
        check_fields(source, ("reasoning",))
        reasoning = _object(source.get("reasoning"))
        check_fields(reasoning, ("effort",))
        effort = _string(reasoning.get("effort"))
        if not effort:
            raise ValueError("reasoning effort must not be empty")
        return {**result, "reasoning": {"effort": effort}}
    if kind not in _STRINGS:
        return {"type": "other"}
    required, optional = _STRINGS[kind]
    check_fields(
        source,
        (
            "id",
            *required,
            *optional,
            *_EXTRA.get(kind, ()),
            METADATA,
        ),
    )
    for field in required:
        result[field] = _string(source.get(field))
    for field in ("id", *optional):
        _optional(source, result, field)
    _optional(source, result, METADATA, response_metadata_payload)
    if kind == "message":
        result["content"] = _parts(source.get("content"), _TEXT | _MEDIA)
        _optional(source, result, "phase", lambda v: _enum(v, ("commentary", "final_answer")))
    elif kind == "agent_message":
        result["content"] = _parts(source.get("content"), {"input_text": "text"} | _ENCRYPTED)
    elif kind == "reasoning":
        result["summary"] = _parts(source.get("summary"), {"summary_text": "text"})
        _optional(source, result, "encrypted_content", emit_null=True)
        content = source.get("content")
        if content is None:
            result["content"] = None
        else:
            content = _parts(content, {"reasoning_text": "text", "text": "text"})
            if any(part["type"] == "reasoning_text" for part in content):
                result["content"] = content
    elif kind == "local_shell_call":
        _optional(source, result, "call_id", emit_null=True)
        result["status"] = _enum(source.get("status"), ("completed", "in_progress", "incomplete"))
        result["action"] = _shell_action(source.get("action"))
    elif kind == "function_call":
        _optional(source, result, "encrypted_function_args", _strings)
    elif kind in ("function_call_output", "custom_tool_call_output"):
        output = source.get("output")
        result["output"] = (
            output
            if isinstance(output, str)
            else _parts(output, {"input_text": "text"} | _MEDIA | _ENCRYPTED)
        )
    elif kind == "web_search_call":
        _optional(source, result, "action", _web_action)
    elif kind == "tool_search_call":
        _optional(source, result, "call_id", emit_null=True)
        if "arguments" not in source:
            raise ValueError("missing tool_search_call.arguments")
        result["arguments"] = materialize(source["arguments"], preserve_pairs=False)
    elif kind == "tool_search_output":
        _optional(source, result, "call_id", emit_null=True)
        result["tools"] = materialize(_array(source.get("tools")), preserve_pairs=False)
    return result


def response_item_from_value(value):
    """The SSE Value-to-typed boundary, distinct from raw legacy deserialization."""
    validate_buffered_value(value)
    return response_item_payload(value)
