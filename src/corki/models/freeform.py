"""Raw input wire adaptation; this module does not execute JavaScript."""

import json
from dataclasses import replace

from corki.models.namespaces import request_tool_aliases
from corki.models.types import ModelRequest
from corki.protocol.items import ToolResultItem
from corki.protocol.tools import ToolCall


def compatible_call(call: ToolCall, request: ModelRequest) -> ToolCall:
    call = replace(call, name=request_tool_aliases(request).get(call.name, call.name))
    specs = {
        spec.name: spec
        for item in request.items
        if isinstance(item, ToolResultItem) and not item.is_error
        for spec in item.discovered_tools
    }
    specs.update((spec.name, spec) for spec in request.tools)
    spec = specs.get(call.name)
    if spec is None or spec.input_kind != "freeform":
        return call
    args = call.arguments
    if (
        call.parse_error is None
        and args is not None
        and set(args) == {"input"}
        and isinstance(args["input"], str)
    ):
        return replace(call, arguments=None, raw_arguments=args["input"], input_kind="freeform")
    return replace(
        call,
        arguments=None,
        input_kind="freeform",
        parse_error=call.parse_error or "freeform arguments require exactly one string input",
    )


def compatible_arguments(call: ToolCall) -> str:
    if call.input_kind == "freeform" and call.parse_error is None:
        return json.dumps({"input": call.raw_arguments}, ensure_ascii=False)
    if call.parse_error is not None:
        return call.raw_arguments
    return call.raw_arguments or json.dumps(dict(call.arguments or {}), ensure_ascii=False)
