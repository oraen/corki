"""Typed MRTR input-request matching only; never perform the requested action."""

import math

from corki.mcp.content_blocks import content_block
from corki.mcp.elicitation import standard_request
from corki.mcp.json_rpc import MCPProtocolError
from corki.mcp.json_values import json_value
from corki.mcp.wire_types import (
    boolean,
    enum,
    invalid,
    literal,
    mapping,
    metadata,
    shape,
    text,
    u32,
    vector,
)
from corki.protocol.wire_json import WireObject, object_pairs


def _float(value):
    # Content's typed float visitor accepts numeric primitives, not the private
    # number map produced by raw decimal syntax with arbitrary_precision.
    if type(value) not in (int, float) or not math.isfinite(value):
        invalid()


def _sampling_block(value):
    shape({"type": text})(value)
    kind = value["type"]
    if kind in ("text", "image", "audio"):
        content_block(value)
    elif kind == "tool_use":
        shape({"type": text, "id": text, "name": text, "input": metadata}, {"_meta": metadata})(
            value
        )
    elif kind == "tool_result":
        shape(
            {"type": text, "toolUseId": text, "content": vector(content_block)},
            {"_meta": metadata, "structuredContent": json_value, "isError": boolean},
        )(value)
    else:
        invalid()


def _sampling_content(value):
    if isinstance(value, list):
        vector(_sampling_block)(value)
    else:
        _sampling_block(value)


def _sampling(value, tool_validator):
    shape(
        {
            "messages": vector(
                shape(
                    {"role": enum("user", "assistant"), "content": _sampling_content},
                    {"_meta": metadata},
                    sequence=("role", "content", "_meta"),
                )
            ),
            "maxTokens": u32,
        },
        {
            "modelPreferences": shape(
                optional={
                    "hints": vector(shape(optional={"name": text}, sequence=("name",))),
                    "costPriority": _float,
                    "speedPriority": _float,
                    "intelligencePriority": _float,
                },
                sequence=("hints", "costPriority", "speedPriority", "intelligencePriority"),
            ),
            "systemPrompt": text,
            "includeContext": enum("allServers", "none", "thisServer"),
            "temperature": _float,
            "stopSequences": vector(text),
            "metadata": json_value,
            "tools": vector(tool_validator),
            "toolChoice": shape(
                optional={"mode": enum("auto", "required", "none")}, sequence=("mode",)
            ),
        },
    )(value)


def _input_request(value, tool_validator):
    value = shape(
        {"method": literal("sampling/createMessage", "elicitation/create", "roots/list")},
        {"params": lambda item: shape()(item)},
        sequence=("method", "params"),
    )(value)
    method, params = value["method"], value.get("params")
    if params is None:
        if method == "roots/list":
            return
        invalid()
    # WithMeta removes its typed field before flattening the remaining params.
    shape(optional={"_meta": metadata})(params)
    rest = WireObject((key, item) for key, item in object_pairs(params) if key != "_meta")
    if method == "roots/list":
        metadata(rest)
    elif method == "sampling/createMessage":
        _sampling(rest, tool_validator)
    else:
        try:
            standard_request(rest)
        except ValueError:
            raise MCPProtocolError("Invalid MCP input request") from None


def validate_input_requests(value, *, tool_validator):
    """Every map occurrence must decode, even if a later request reuses its key."""
    mapping(lambda item: _input_request(item, tool_validator))(value)
