"""Codex tool-schema normalization, typed projection and best-effort compaction.

Only schema-valued children are rewritten. Instance data (notably enum values)
is not a schema, even when its objects have keys such as $ref or description.
"""

import re
from collections.abc import Mapping
from copy import deepcopy
from urllib.parse import unquote

from corki.protocol.wire_numbers import dumps_wire

DEFINITIONS = ("$defs", "definitions")
COMPOSITIONS = ("anyOf", "oneOf", "allOf")
CHILDREN = ("items", *COMPOSITIONS)
TYPES = frozenset(("string", "number", "boolean", "integer", "object", "array", "null"))
FIELDS = (
    "$ref",
    "type",
    "description",
    "encrypted",
    "enum",
    "items",
    "minItems",
    "properties",
    "required",
    "additionalProperties",
    *COMPOSITIONS,
    *DEFINITIONS,
)


class MCPInputSchemaError(ValueError):
    """A valid raw MCP tool cannot be represented as a model input schema."""


def normalized_input_schema(schema, *, compact=True):
    """Match parse_tool_input_schema; MCP's top-level properties fix is separate."""
    value = _sanitize(deepcopy(schema))
    _prune_definitions(value)
    if compact:
        for operation in ("descriptions", "definitions", "depth", "compositions"):
            try:
                size = json_bytes(_project(value))
            except (TypeError, ValueError):
                # Native accounting returns zero on typed-deserialization error;
                # malformed schemas are rejected below, not rescued by compaction.
                break
            if size <= 5000:
                break
            value = _compact(value, operation)
            if operation == "definitions" and isinstance(value, dict):
                for key in DEFINITIONS:
                    value.pop(key, None)
    result = _project(value)
    if result.get("type") == "null":
        raise ValueError("tool input schema must not be a singleton null type")
    return result


def mcp_input_schema(schema):
    value = deepcopy(dict(schema))
    if value.get("properties") is None:
        value["properties"] = {}
    try:
        return normalized_input_schema(value)
    except (TypeError, ValueError, RecursionError) as error:
        raise MCPInputSchemaError(str(error)) from error


def json_bytes(value):
    return len(dumps_wire(value).encode("utf-8"))


def agent_parameters(parameters, name, description):
    """Count the renamed inner ResponsesApiTool, not a namespace/type wrapper."""
    size = json_bytes(
        {"name": name, "description": description, "strict": False, "parameters": parameters}
    )
    if size > 8000:
        return {"type": "object", "properties": {}, "additionalProperties": True}
    return parameters


def _sanitize(value):
    if type(value) is bool:
        return {"type": "string"}
    if isinstance(value, list):
        return [_sanitize(child) for child in value]
    if not isinstance(value, Mapping):
        return value
    value = dict(value)
    if isinstance(value.get("properties"), Mapping):
        value["properties"] = {key: _sanitize(child) for key, child in value["properties"].items()}
    for key in (*CHILDREN, "prefixItems", "additionalProperties"):
        if key in value and not (key == "additionalProperties" and type(value[key]) is bool):
            value[key] = _sanitize(value[key])
    for key in DEFINITIONS:
        if key in value:
            if isinstance(value[key], Mapping):
                value[key] = {name: _sanitize(child) for name, child in value[key].items()}
            else:
                del value[key]
    if "const" in value:
        value["enum"] = [value.pop("const")]
    raw = value.get("type")
    types = [
        kind
        for kind in (raw if isinstance(raw, list) else [raw])
        if isinstance(kind, str) and kind in TYPES
    ]
    if not types and ("$ref" in value or any(key in value for key in COMPOSITIONS)):
        return value
    if not types:
        hints = (
            ("object", ("properties", "required", "additionalProperties")),
            ("array", ("items", "prefixItems")),
            ("string", ("enum", "format")),
            (
                "number",
                ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"),
            ),
        )
        types = next(([kind] for kind, keys in hints if any(key in value for key in keys)), [])
        if not types:
            return {}
    value["type"] = types[0] if len(types) == 1 else types
    if "object" in types:
        value.setdefault("properties", {})
    if "array" in types:
        value.setdefault("items", {"type": "string"})
    return value


def _project(value):
    if not isinstance(value, Mapping):
        raise ValueError("invalid tool input schema: expected schema object")
    result = {}
    for key in FIELDS:
        item = value.get(key)
        if item is None:
            continue
        valid = True
        if key in ("$ref", "description"):
            valid = isinstance(item, str)
        elif key == "type":
            types = item if isinstance(item, list) else [item]
            valid = all(isinstance(kind, str) and kind in TYPES for kind in types)
        elif key == "encrypted":
            valid = type(item) is bool
        elif key == "enum":
            valid = isinstance(item, list)
        elif key == "minItems":
            valid = type(item) is int and 0 <= item < 2**64
        elif key == "required":
            valid = isinstance(item, list) and all(isinstance(name, str) for name in item)
        elif key in ("properties", *DEFINITIONS):
            valid = isinstance(item, Mapping) and all(isinstance(name, str) for name in item)
            if valid:
                item = {name: _project(item[name]) for name in sorted(item)}
        elif key in COMPOSITIONS:
            valid = isinstance(item, list)
            if valid:
                item = [_project(child) for child in item]
        elif key == "items" or (key == "additionalProperties" and type(item) is not bool):
            item = _project(item)
        if not valid:
            raise ValueError(f"invalid tool input schema field: {key}")
        result[key] = item
    return result


def _children(value, *, definitions=False):
    """Yield mutable (container, key) slots; prefixItems is intentionally absent."""
    properties = value.get("properties")
    if isinstance(properties, dict):
        yield from ((properties, key) for key in properties)
    for key in CHILDREN:
        if key in value:
            yield value, key
    if "additionalProperties" in value and type(value["additionalProperties"]) is not bool:
        yield value, "additionalProperties"
    if definitions:
        for key in DEFINITIONS:
            table = value.get(key)
            if isinstance(table, dict):
                yield from ((table, name) for name in table)


def _local_ref(value):
    if not isinstance(value, str) or not value.startswith("#"):
        return None
    try:
        pointer = unquote(value[1:], errors="strict")
    except UnicodeError:
        return None
    if not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
        return None
    tokens = [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]
    if len(tokens) >= 2 and tokens[0] in DEFINITIONS:
        return tokens[0], tokens[1]
    return None


def _refs(value, *, all_values=False):
    if isinstance(value, list):
        for child in value:
            yield from _refs(child, all_values=all_values)
    elif isinstance(value, dict):
        reference = _local_ref(value.get("$ref"))
        if reference is not None:
            yield reference
        children = (
            value.values() if all_values else (parent[key] for parent, key in _children(value))
        )
        for child in children:
            yield from _refs(child, all_values=all_values)


def _prune_definitions(value):
    if not isinstance(value, dict):
        return
    reachable, pending = set(), list(_refs(value))
    while pending:
        pointer = pending.pop()
        if pointer in reachable:
            continue
        reachable.add(pointer)
        table, name = pointer
        definition = value.get(table, {}).get(name)
        pending.extend(_refs(definition, all_values=True))
    for table in DEFINITIONS:
        if table in value:
            value[table] = {
                name: child for name, child in value[table].items() if (table, name) in reachable
            }
            if not value[table]:
                del value[table]


def _compact(value, operation, depth=0):
    if isinstance(value, list):
        return [_compact(child, operation, depth) for child in value]
    if not isinstance(value, dict):
        return value
    if operation == "descriptions":
        value.pop("description", None)
    elif (
        (operation == "definitions" and _local_ref(value.get("$ref")) is not None)
        or (
            operation == "depth"
            and depth >= 3
            and any(
                key in value for key in (*CHILDREN, "properties", "additionalProperties", "$ref")
            )
        )
        or (operation == "compositions" and any(key in value for key in COMPOSITIONS))
    ):
        return {}
    for parent, key in _children(value, definitions=operation == "descriptions"):
        parent[key] = _compact(parent[key], operation, depth + 1)
    return value
