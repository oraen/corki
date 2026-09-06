"""Tool dispatch, schema validation, error normalization, and output budgeting."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from corki.context.truncation import truncate_text
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.tools.base import ToolContext
from corki.tools.registry import ToolRegistry
from corki.tools.search import ToolSearchTool


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, *, output_char_budget: int) -> None:
        self._registry = registry
        self._output_char_budget = output_char_budget

    async def execute(
        self, call: ToolCall, context: ToolContext, *, spec: ToolSpec | None = None
    ) -> ToolResult:
        tool = self._registry.get(call.name)
        if tool is None:
            return self.error(call, f"unknown tool: {call.name}")
        if call.parse_error is not None or call.arguments is None:
            return self.error(call, f"invalid JSON arguments: {call.parse_error}")
        current = self._registry.spec(call.name)
        if spec is not None and spec != current:
            return self.error(call, f"tool definition changed since this step: {call.name}")
        spec = spec or current
        try:
            _validate(call.arguments, spec.parameters, path="arguments")
            result = await tool.execute(call, context)
        except Exception as exc:  # noqa: BLE001 - tool boundary normalizes failures
            return self.error(call, f"{type(exc).__name__}: {exc}")
        if not isinstance(result, ToolResult):
            return self.error(
                call,
                f"tool returned {type(result).__name__} instead of ToolResult",
            )
        if result.call_id != call.id or result.tool_name != call.name:
            return self.error(call, "tool returned a result for a different call")
        output_budget = spec.output_char_budget or self._output_char_budget
        is_search = isinstance(tool, ToolSearchTool)
        return ToolResult(
            call_id=result.call_id,
            tool_name=result.tool_name,
            # Search already budgets whole schemas. Never publish a truncated
            # definition while loading the complete one for execution.
            content=result.content if is_search else truncate_text(result.content, output_budget),
            is_error=result.is_error,
            display_content=truncate_text(
                result.display_content if result.display_content is not None else result.content,
                min(output_budget, 4_000),
            ),
            attachments=result.attachments,
            state_update=result.state_update,
            discovered_tools=result.discovered_tools if is_search else (),
        )

    @staticmethod
    def error(call: ToolCall, message: str) -> ToolResult:
        """Build a normalized failure without invoking a registered handler."""

        return ToolResult(
            call_id=call.id,
            tool_name=call.name,
            content=message,
            display_content=message,
            is_error=True,
        )


def _validate(value: object, schema: Mapping[str, Any], *, path: str) -> None:
    """Validate the JSON-Schema subset used by Corki's built-in tools."""

    expected = schema.get("type")
    type_map: dict[str, type | tuple[type, ...]] = {
        "object": Mapping,
        "array": list,
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
    }
    if isinstance(expected, str) and expected in type_map:
        expected_type = type_map[expected]
        if not isinstance(value, expected_type) or (
            expected in {"integer", "number"} and isinstance(value, bool)
        ):
            raise ValueError(f"{path} must be {expected}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} must be one of {schema['enum']}")
    if isinstance(value, Mapping):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        missing = [key for key in required if key not in value]
        if missing:
            raise ValueError(f"{path} missing required fields: {', '.join(missing)}")
        if schema.get("additionalProperties") is False:
            extras = set(value).difference(properties)
            if extras:
                raise ValueError(f"{path} has unknown fields: {', '.join(sorted(extras))}")
        for key, item in value.items():
            child = properties.get(key)
            if isinstance(child, Mapping):
                _validate(item, child, path=f"{path}.{key}")
    if isinstance(value, list) and isinstance(schema.get("items"), Mapping):
        for index, item in enumerate(value):
            _validate(item, schema["items"], path=f"{path}[{index}]")
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise ValueError(f"{path} requires at least {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ValueError(f"{path} allows at most {schema['maxItems']} items")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"{path} must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"{path} must be <= {schema['maximum']}")
