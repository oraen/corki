"""Structural projection of RMCP's five typed tool-result content variants."""

from collections.abc import Mapping

from corki.mcp.float_projection import project_f32
from corki.mcp.json_rpc import MCPProtocolError, check_known_fields
from corki.mcp.json_values import metadata_map
from corki.mcp.wire_types import enum, struct_object
from corki.protocol.wire_json import WireObject
from corki.protocol.wire_numbers import WireNumber

_role = enum("user", "assistant")
_theme = enum("light", "dark")


def _invalid():
    # A malformed field must not reflect private remote data into observations.
    raise MCPProtocolError("MCP result.content contains an invalid content block")


def _text(value):
    if not isinstance(value, str):
        _invalid()
    try:
        value.encode("utf-8")
    except UnicodeError:
        _invalid()
    return value


def _object(value, fields=()):
    if not isinstance(value, Mapping):
        _invalid()
    for key in value:
        _text(key)
    check_known_fields(value, fields)
    return value


def _strings(value):
    if not isinstance(value, list):
        _invalid()
    return [_text(item) for item in value]


def _optional_text(source, target, *keys):
    for key in keys:
        if source.get(key) is not None:
            target[key] = _text(source[key])


def _meta(source, target):
    if source.get("_meta") is not None:
        target["_meta"] = metadata_map(_object(source["_meta"]))


def _annotations(source, target):
    if source.get("annotations") is None:
        return
    fields = ("audience", "priority", "lastModified")
    raw = _object(struct_object(source["annotations"], fields), fields)
    result = {}
    if raw.get("audience") is not None:
        if not isinstance(raw["audience"], list):
            _invalid()
        result["audience"] = [_role(role) for role in raw["audience"]]
    if raw.get("priority") is not None:
        if isinstance(raw, WireObject) and isinstance(raw["priority"], WireNumber):
            _invalid()
        try:
            result["priority"] = project_f32(raw["priority"])
        except ValueError:
            _invalid()
    _optional_text(raw, result, "lastModified")
    target["annotations"] = result


def _icon(value):
    fields = ("src", "mimeType", "sizes", "theme")
    raw = _object(struct_object(value, fields), fields)
    result = {"src": _text(raw.get("src"))}
    _optional_text(raw, result, "mimeType")
    if raw.get("sizes") is not None:
        result["sizes"] = _strings(raw["sizes"])
    if raw.get("theme") is not None:
        result["theme"] = _theme(raw["theme"])
    return result


def _resource(value):
    raw = _object(value)
    result = {"uri": _text(raw.get("uri"))}
    _optional_text(raw, result, "mimeType")
    _meta(raw, result)
    # An invalid text variant may still be a valid blob variant. Unknown fields
    # in the selected variant disappear, rather than entering nested model data.
    common = {"uri", "mimeType", "_meta"}
    try:
        check_known_fields(raw, common | {"text"})
        result["text"] = _text(raw.get("text"))
        return result
    except MCPProtocolError:
        pass
    check_known_fields(raw, common | {"blob"})
    result["blob"] = _text(raw.get("blob"))
    return result


def content_block(value):
    """Validate and detach one block before capability filtering or conversion."""
    raw = _object(value)
    kind = raw.get("type")
    if kind not in ("text", "image", "audio", "resource", "resource_link"):
        _invalid()
    fields = {
        "text": {"text"},
        "image": {"data", "mimeType"},
        "audio": {"data", "mimeType"},
        "resource": {"resource"},
        "resource_link": {"uri", "name", "title", "description", "mimeType", "size", "icons"},
    }
    check_known_fields(raw, fields[kind] | {"type", "_meta", "annotations"})
    result = {"type": kind}
    if kind == "text":
        result["text"] = _text(raw.get("text"))
    elif kind in ("image", "audio"):
        result.update(data=_text(raw.get("data")), mimeType=_text(raw.get("mimeType")))
    elif kind == "resource":
        result["resource"] = _resource(raw.get("resource"))
    else:
        result.update(uri=_text(raw.get("uri")), name=_text(raw.get("name")))
        _optional_text(raw, result, "title", "description", "mimeType")
        size = raw.get("size")
        if size is not None:
            if type(size) is not int or not 0 <= size < 2**64:
                _invalid()
            result["size"] = size
        if raw.get("icons") is not None:
            if not isinstance(raw["icons"], list):
                _invalid()
            result["icons"] = [_icon(icon) for icon in raw["icons"]]
    _meta(raw, result)
    _annotations(raw, result)
    return result
