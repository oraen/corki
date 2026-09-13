"""Typed candidates preceding CallToolResult in RMCP's raw ServerResult union.

This is transport classification, not tool schema validation or dispatch. A failed
candidate must leave the original pairs intact for the following candidate.
"""

from corki.mcp.content_blocks import _annotations, _icon, _resource, content_block
from corki.mcp.input_requests import validate_input_requests
from corki.mcp.json_rpc import MCPProtocolError
from corki.mcp.json_values import json_value
from corki.mcp.wire_types import boolean as _boolean
from corki.mcp.wire_types import enum as _enum
from corki.mcp.wire_types import i64 as _i64
from corki.mcp.wire_types import invalid as _invalid
from corki.mcp.wire_types import literal as _literal
from corki.mcp.wire_types import mapping as _map
from corki.mcp.wire_types import metadata as _metadata
from corki.mcp.wire_types import object_value as _object
from corki.mcp.wire_types import shape as _shape
from corki.mcp.wire_types import struct_object as _struct_object
from corki.mcp.wire_types import text as _text
from corki.mcp.wire_types import u32 as _u32
from corki.mcp.wire_types import u64 as _u64
from corki.mcp.wire_types import vector as _vector
from corki.protocol.wire_json import WireObject, object_pairs

_icons = _vector(_icon)
_cache_scope = _enum("public", "private")
_common = {"resultType": _text, "_meta": _metadata}
_cache = {"ttlMs": _i64, "cacheScope": _cache_scope}
_named = {"title": _text, "description": _text, "icons": _icons, "_meta": _metadata}
_implementation = _shape(
    {"name": _text, "version": _text},
    {"title": _text, "description": _text, "icons": _icons, "websiteUrl": _text},
    sequence=("name", "title", "version", "description", "icons", "websiteUrl"),
)
_capabilities = _shape(
    optional={
        "experimental": _map(_metadata),
        "extensions": _map(_metadata),
        "logging": _metadata,
        "completions": _metadata,
        "tools": _shape(optional={"listChanged": _boolean}, sequence=("listChanged",)),
        "prompts": _shape(optional={"listChanged": _boolean}, sequence=("listChanged",)),
        "resources": _shape(
            optional={"listChanged": _boolean, "subscribe": _boolean},
            sequence=("subscribe", "listChanged"),
        ),
    },
    sequence=(
        "experimental",
        "extensions",
        "logging",
        "completions",
        "prompts",
        "resources",
        "tools",
    ),
)
_initialize = _shape(
    {
        "protocolVersion": _text,
        "capabilities": _capabilities,
        "serverInfo": _implementation,
    },
    {"instructions": _text, "_meta": _metadata},
    sequence=("protocolVersion", "capabilities", "serverInfo", "instructions", "_meta"),
)
_tool_annotation_fields = (
    "title",
    "readOnlyHint",
    "destructiveHint",
    "idempotentHint",
    "openWorldHint",
)
_tool_annotations = _shape(
    optional={
        "title": _text,
        "readOnlyHint": _boolean,
        "destructiveHint": _boolean,
        "idempotentHint": _boolean,
        "openWorldHint": _boolean,
    },
    sequence=_tool_annotation_fields,
)
_tool = _shape(
    {"name": _text, "inputSchema": _metadata},
    {
        **_named,
        "outputSchema": _metadata,
        "annotations": _tool_annotations,
    },
    sequence=(
        "name",
        "title",
        "description",
        "inputSchema",
        "outputSchema",
        "annotations",
        "icons",
        "_meta",
    ),
)
_prompt = _shape(
    {"name": _text},
    {
        **_named,
        "arguments": _vector(
            _shape(
                {"name": _text},
                {"title": _text, "description": _text, "required": _boolean},
                sequence=("name", "title", "description", "required"),
            )
        ),
    },
    sequence=("name", "title", "description", "arguments", "icons", "_meta"),
)


def _resource_definition(value):
    # Resource is the inner resource_link struct: its unknown `type` is ignored.
    value = _struct_object(
        value,
        (
            "uri",
            "name",
            "title",
            "description",
            "mimeType",
            "size",
            "icons",
            "_meta",
            "annotations",
        ),
    )
    pairs = [("type", "resource_link")]
    pairs.extend((key, item) for key, item in object_pairs(_object(value)) if key != "type")
    result = content_block(WireObject(pairs))
    result.pop("type")
    return result


def _resource_template(value):
    value = _shape(
        {"uriTemplate": _text, "name": _text},
        {
            **_named,
            "mimeType": _text,
            "annotations": lambda item: _annotations({"annotations": item}, {}),
        },
        sequence=(
            "uriTemplate",
            "name",
            "title",
            "description",
            "mimeType",
            "icons",
            "_meta",
            "annotations",
        ),
    )(value)
    result = {"uriTemplate": value["uriTemplate"], "name": value["name"]}
    for key in ("title", "description", "mimeType"):
        if value.get(key) is not None:
            result[key] = value[key]
    if value.get("icons") is not None:
        result["icons"] = [_icon(icon) for icon in value["icons"]]
    if value.get("_meta") is not None:
        result["_meta"] = _metadata(value["_meta"])
    _annotations(value, result)
    return result


def _subscription(value):
    value = _shape({"resultType": _text, "_meta": _metadata}, sequence=("resultType", "_meta"))(
        value
    )
    metadata = _metadata(value["_meta"])
    identity = metadata.get("io.modelcontextprotocol/subscriptionId")
    if not isinstance(identity, str):
        _i64(identity)


_task_fields = {
    "taskId": _text,
    "status": _enum("working", "input_required", "completed", "failed", "cancelled"),
    "createdAt": _text,
    "lastUpdatedAt": _text,
}
_task_optional = {"statusMessage": _text, "ttlMs": _u64, "pollIntervalMs": _u64}


def _create_task(value):
    _shape(
        {"resultType": _literal("task"), **_task_fields},
        {"_meta": _metadata, **_task_optional},
    )(value)


def _get_task(value):
    _object(value)  # Flattened DetailedTask has no sequence representation.
    # serde(default) applies only to absence, unlike Option's explicit null.
    if "resultType" in value:
        _text(value["resultType"])
    _shape(
        _task_fields,
        {
            **_common,
            **_task_optional,
            "inputRequests": lambda item: validate_input_requests(item, tool_validator=_tool),
            "result": _metadata,
            "error": _metadata,
        },
    )(value)
    required = {"input_required": "inputRequests", "completed": "result", "failed": "error"}
    key = required.get(_task_fields["status"](value["status"]))
    if key is not None and value.get(key) is None:
        _invalid()


def _paginated(field, item):
    return _shape(
        {field: _vector(item)},
        {**_common, **_cache, "nextCursor": _text},
        sequence=("resultType", "_meta", "nextCursor", "ttlMs", "cacheScope", field),
    )


_tools_result = _paginated("tools", _tool)
_resources_result = _paginated("resources", _resource_definition)
_templates_result = _paginated("resourceTemplates", _resource_template)
_read_result = _shape(
    {"contents": _vector(_resource)},
    {**_common, **_cache},
    sequence=("resultType", "ttlMs", "cacheScope", "contents", "_meta"),
)

# Declaration order is protocol behavior, not the order of keys in the response.
_CANDIDATES = (
    (
        "DiscoverResult",
        _shape(
            {
                "resultType": _text,
                "supportedVersions": _vector(_text),
                "capabilities": _capabilities,
                "ttlMs": _u64,
                "cacheScope": _cache_scope,
            },
            {"instructions": _text, "_meta": _metadata},
            sequence=(
                "resultType",
                "supportedVersions",
                "capabilities",
                "instructions",
                "ttlMs",
                "cacheScope",
                "_meta",
            ),
        ),
    ),
    ("InitializeResult", _initialize),
    (
        "CompleteResult",
        _shape(
            {
                "completion": _shape(
                    {"values": _vector(_text)},
                    {"total": _u32, "hasMore": _boolean},
                    sequence=("values", "total", "hasMore"),
                )
            },
            _common,
            sequence=("resultType", "completion", "_meta"),
        ),
    ),
    (
        "GetPromptResult",
        _shape(
            {
                "messages": _vector(
                    _shape(
                        {"role": _enum("user", "assistant"), "content": content_block},
                        sequence=("role", "content"),
                    )
                )
            },
            {**_common, "description": _text},
            sequence=("resultType", "description", "messages", "_meta"),
        ),
    ),
    ("ListPromptsResult", _paginated("prompts", _prompt)),
    ("ListResourcesResult", _resources_result),
    ("ListResourceTemplatesResult", _templates_result),
    ("ReadResourceResult", _read_result),
    ("SubscriptionsListenResult", _subscription),
    ("ListToolsResult", _tools_result),
    (
        "ElicitResult",
        _shape(
            {"action": _enum("accept", "decline", "cancel")},
            {"content": json_value, "_meta": _metadata},
            sequence=("action", "content", "_meta"),
        ),
    ),
    ("CreateTaskResult", _create_task),
    ("GetTaskResult", _get_task),
)


def preceding_tool_result(value):
    """Return a successfully decoded earlier variant, without mutating raw JSON."""
    for name, validate in _CANDIDATES:
        try:
            validate(value)
        except MCPProtocolError:
            continue
        return name
    return None


def initialization_views(value):
    """Select InitializeResult, then expose validated raw views to its consumer.

    DiscoverResult precedes initialization even during a legacy handshake. These
    views retain nested map/Value provenance; they are not public JSON results.
    """
    if preceding_tool_result(value) != "InitializeResult":
        _invalid()
    result = _initialize(value)
    return result, _capabilities(result["capabilities"])


def listed_tools(value):
    """Select and detach a complete typed tool list before registration/caching.

    Unknown struct fields disappear, while schema/metadata maps keep their own
    JSON Value semantics. One malformed element invalidates the entire result.
    """
    if preceding_tool_result(value) != "ListToolsResult":
        _invalid()
    result = _tools_result(value)
    tools = []
    for item in result["tools"]:
        raw = _tool(item)
        tool = {"name": raw["name"], "inputSchema": _metadata(raw["inputSchema"])}
        for key in ("title", "description"):
            if raw.get(key) is not None:
                tool[key] = raw[key]
        for key in ("outputSchema", "_meta"):
            if raw.get(key) is not None:
                tool[key] = _metadata(raw[key])
        if raw.get("annotations") is not None:
            annotations = _tool_annotations(raw["annotations"])
            tool["annotations"] = {
                key: annotations[key]
                for key in _tool_annotation_fields
                if annotations.get(key) is not None
            }
        if raw.get("icons") is not None:
            tool["icons"] = [_icon(icon) for icon in raw["icons"]]
        tools.append(tool)
    return tuple(tools)


def resource_result(value, kind: str) -> dict:
    """Detach one typed list/read response, without interpreting its cursor."""
    variant, field, validate, project = {
        "resources": ("ListResourcesResult", "resources", _resources_result, _resource_definition),
        "templates": (
            "ListResourceTemplatesResult",
            "resourceTemplates",
            _templates_result,
            _resource_template,
        ),
        "read": ("ReadResourceResult", "contents", _read_result, _resource),
    }[kind]
    if preceding_tool_result(value) != variant:
        _invalid()
    raw = validate(value)
    result = {field: [project(item) for item in raw[field]]}
    for key in ("resultType", "nextCursor"):
        if raw.get(key) is not None and (key != "nextCursor" or kind != "read"):
            result[key] = raw[key]
    if raw.get("ttlMs") is not None:
        result["ttlMs"] = max(0, raw["ttlMs"])
    if raw.get("cacheScope") is not None:
        result["cacheScope"] = _cache_scope(raw["cacheScope"])
    if raw.get("_meta") is not None:
        result["_meta"] = _metadata(raw["_meta"])
    return result
