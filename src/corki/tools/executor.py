"""Tool dispatch, schema validation, error normalization, and output budgeting."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from typing import Any

from corki.context.hosted_output import truncate_output_text
from corki.context.truncation import truncate_text
from corki.planning.models import validate_plan
from corki.protocol.tools import (
    AudioAttachment,
    CodeModeOutput,
    EncryptedContent,
    ImageAttachment,
    TextContent,
    ToolCall,
    ToolResult,
    ToolSpec,
    ToolStateUpdate,
    content_text,
    same_tool_spec,
    tool_spec_to_payload,
)
from corki.protocol.truncation import TruncationPolicy
from corki.protocol.wire_numbers import dumps_wire
from corki.tools.base import ToolCallArgumentParser, ToolContext
from corki.tools.errors import FatalToolError
from corki.tools.registry import ToolRegistry, ToolRegistrySnapshot
from corki.tools.search import ToolSearchTool

MAX_RAW_TOOL_RESULT_BYTES = 32_000_000


class ToolExecutor:
    def __init__(
        self, registry: ToolRegistry, *, output_char_budget: int, media_preparation=None
    ) -> None:
        self._registry = registry
        self._output_char_budget = output_char_budget
        self._media = media_preparation

    async def execute(
        self,
        call: ToolCall,
        context: ToolContext,
        *,
        spec: ToolSpec | None = None,
        snapshot: ToolRegistrySnapshot | None = None,
    ) -> ToolResult:
        registry = snapshot if snapshot is not None else self._registry
        tool = registry.get(call.name)
        if tool is None:
            return self.error(call, f"unknown tool: {call.name}")
        current = registry.spec(call.name)
        if spec is not None and not same_tool_spec(spec, current):
            return self.error(call, f"tool definition changed since this step: {call.name}")
        spec = spec or current
        if spec is None:
            raise FatalToolError(f"registered tool has no definition: {call.name}")
        if call.input_kind != spec.input_kind:
            raise FatalToolError(f"tool {call.name} invoked with incompatible payload")
        execution_input = call
        if context.before_tool is not None:
            execution_input = await context.before_tool(deepcopy(call), tool)
            if isinstance(execution_input, str):
                return self.error(call, execution_input, spec=spec)
        task = asyncio.current_task()
        cancellations = task.cancelling() if task is not None else 0
        try:
            execution_call = deepcopy(execution_input)
            if call.input_kind == "json" and isinstance(tool, ToolCallArgumentParser):
                arguments = tool.parse_call_arguments(execution_call)
                if arguments is not None and not isinstance(arguments, Mapping):
                    raise FatalToolError("tool call parser did not return an argument object")
                execution_call = replace(
                    execution_call,
                    arguments=deepcopy(dict(arguments)) if arguments is not None else None,
                    parse_error=None,
                )
            elif execution_call.parse_error is not None or (
                call.input_kind == "json" and execution_call.arguments is None
            ):
                return self.error(call, f"invalid JSON arguments: {call.parse_error}", spec=spec)
            elif call.input_kind == "json":
                _validate(execution_call.arguments, spec.parameters, path="arguments")
            else:
                _text(call.raw_arguments, "input")
            # The handler cannot mutate the durable model call's arguments
            # through nested dict/list aliases after the ledger was claimed.
            execution_input_json = dumps_wire(
                {
                    "version": 1,
                    "input_kind": execution_call.input_kind,
                    "arguments": execution_call.arguments,
                    "raw_arguments": execution_call.raw_arguments,
                }
            )
            result = await tool.execute(execution_call, context)
            result = self._normalize_result(
                call,
                result,
                spec,
                is_search=isinstance(tool, ToolSearchTool),
            )
            result = replace(result, execution_input_json=execution_input_json)
            return await self._media.prepare_result(result) if self._media is not None else result
        except FatalToolError as exc:
            raise FatalToolError(truncate_text(_exception_message(exc), 4_000)) from exc
        except TimeoutError as exc:
            return self.error(
                call,
                "tool timed out; execution outcome may be unknown. Do not automatically retry "
                f"an operation with side effects. {_exception_message(exc)}",
                spec=spec,
            )
        except Exception as exc:  # noqa: BLE001 - tool boundary normalizes failures
            return self.error(call, _exception_message(exc), spec=spec)
        finally:
            # A handler (or media preparer) may mask CancelledError with a
            # return value or cleanup exception. Do not publish that as a
            # completed result while this execution still has a new cancellation.
            if task is not None and task.cancelling() > cancellations:
                raise asyncio.CancelledError

    def _normalize_result(
        self,
        call: ToolCall,
        result: object,
        spec: ToolSpec,
        *,
        is_search: bool,
    ) -> ToolResult:
        if not isinstance(result, ToolResult):
            raise ValueError(f"tool returned {type(result).__name__} instead of ToolResult")
        if result.call_id != call.id or result.tool_name != call.name:
            raise ValueError("tool returned a result for a different call")
        _text(result.content, "result.content")
        if result.display_content is not None:
            _text(result.display_content, "result.display_content")
        if not isinstance(result.is_error, bool):
            raise ValueError("result.is_error must be a boolean")
        if not isinstance(result.contains_external_context, bool):
            raise ValueError("result.contains_external_context must be a boolean")
        discovered = ()
        if is_search:
            if any(not isinstance(tool, ToolSpec) for tool in result.discovered_tools):
                raise ValueError("discovered tool definitions must be ToolSpec instances")
            # Own the validated definition snapshot, not aliases a handler could
            # mutate after its result has crossed the execution boundary.
            discovered = deepcopy(result.discovered_tools) if not result.is_error else ()
            # Match the durable ledger's encoding before handing it a result.
            # Never stringify unknown values or let invalid Unicode reach SQLite.
            encoded_definitions = dumps_wire(
                [tool_spec_to_payload(tool) for tool in discovered]
            ).encode("utf-8")
            if len(encoded_definitions) > MAX_RAW_TOOL_RESULT_BYTES:
                raise ValueError("discovered tool definitions exceed raw transport limit")
        if result.fallback_token_limit_override is not None and (
            type(result.fallback_token_limit_override) is not int
            or result.fallback_token_limit_override < 0
        ):
            raise ValueError("result.fallback_token_limit_override must be a non-negative integer")
        if not isinstance(result.attachments, (tuple, list)):
            raise ValueError("result.attachments must be an array")
        attachments: list[ImageAttachment] = []
        for attachment in result.attachments:
            if not isinstance(attachment, ImageAttachment):
                raise ValueError("result attachment must be an ImageAttachment")
            _text(attachment.data_url, "attachment.data_url")
            if not attachment.data_url or attachment.detail not in {
                "auto",
                "low",
                "high",
                "original",
            }:
                raise ValueError("result attachment requires a non-empty URL and valid detail")
            attachments.append(ImageAttachment(attachment.data_url, attachment.detail))
        if not isinstance(result.state_update, ToolStateUpdate):
            raise ValueError("result.state_update must be a ToolStateUpdate")
        plan = result.state_update.plan
        if result.state_update.plan_explanation is not None:
            _text(result.state_update.plan_explanation, "plan.explanation")
        if plan is not None:
            if not isinstance(plan, (tuple, list)):
                raise ValueError("result plan must be an array")
            parsed_plan = validate_plan(list(plan))
            for item in parsed_plan:
                _text(item.step, "plan.step")
            plan = tuple(item.as_dict() for item in parsed_plan)
        output_budget = spec.output_char_budget or self._output_char_budget
        parts = result.content_items
        if parts:
            if len(parts) > 8192:
                raise ValueError("tool content item count exceeds limit")
            media_bytes = 0
            if result.attachments or result.discovered_tools:
                raise ValueError(
                    "ordered tool content cannot also use attachments or discovered tools"
                )
            for part in parts:
                if isinstance(part, TextContent):
                    _text(part.text, "content item text")
                elif isinstance(part, EncryptedContent):
                    _text(part.encrypted_content, "encrypted content")
                    media_bytes += len(part.encrypted_content.encode("utf-8"))
                    if media_bytes > 32_000_000:
                        raise ValueError("tool content media byte limit exceeded")
                elif isinstance(part, (ImageAttachment, AudioAttachment)):
                    _text(part.data_url, "content item URL")
                    media_bytes += len(part.data_url.encode("utf-8"))
                    if media_bytes > 32_000_000:
                        raise ValueError("tool content media byte limit exceeded")
                    if not part.data_url:
                        raise ValueError("content item requires a non-empty URL")
                    if isinstance(part, ImageAttachment) and part.detail not in {
                        "auto",
                        "low",
                        "high",
                        "original",
                    }:
                        raise ValueError("invalid content item image detail")
                    if isinstance(part, AudioAttachment) and not part.data_url.lower().startswith(
                        "data:"
                    ):
                        raise ValueError("audio content requires a data URL")
                else:
                    raise ValueError("invalid tool content item")
        # This is a transport/storage guard, not the model's output policy.
        raw_bytes = len(result.content.encode("utf-8")) + sum(
            len(part.text.encode("utf-8"))
            if isinstance(part, TextContent)
            else len(part.encrypted_content.encode("utf-8"))
            if isinstance(part, EncryptedContent)
            else len(part.data_url.encode("utf-8"))
            for part in (*parts, *attachments)
        )
        if raw_bytes > MAX_RAW_TOOL_RESULT_BYTES:
            raise ValueError("tool result exceeds the raw transport byte limit")
        content = content_text(parts) if result.content_items else result.content
        code_mode_output = result.code_mode_output
        if code_mode_output is not None:
            if not isinstance(code_mode_output, CodeModeOutput):
                raise ValueError("result.code_mode_output must be a CodeModeOutput")
            code_mode_output = code_mode_output.normalized()
        return ToolResult(
            call_id=result.call_id,
            tool_name=result.tool_name,
            # History owns the model projection; the ledger and nested calls
            # consume this validated original, not the UI's bounded preview.
            content=content,
            is_error=result.is_error,
            display_content=truncate_text(
                result.display_content if result.display_content is not None else result.content,
                min(output_budget, 4_000),
            ),
            attachments=tuple(attachments),
            state_update=ToolStateUpdate(
                plan=plan,
                new_context_requested=result.state_update.new_context_requested,
                plan_explanation=result.state_update.plan_explanation,
            ),
            discovered_tools=discovered,
            code_mode_output=code_mode_output,
            content_items=parts,
            contains_external_context=result.contains_external_context,
            fallback_token_limit_override=result.fallback_token_limit_override,
            legacy_output_char_budget=spec.output_char_budget,
            is_tool_search_output=is_search,
            mcp_result_json=result.mcp_result_json,
            mcp_error=result.mcp_error,
            patch_delta_json=result.patch_delta_json,
            post_tool_use_json=result.post_tool_use_json,
            code_mode_lifecycle_json=result.code_mode_lifecycle_json,
        )

    def error(self, call: ToolCall, message: str, *, spec: ToolSpec | None = None) -> ToolResult:
        """Build a normalized failure without invoking a registered handler."""
        budget = (spec.output_char_budget if spec is not None else None) or self._output_char_budget
        message = message.encode("utf-8", errors="replace").decode("utf-8")
        if len(message.encode("utf-8")) > MAX_RAW_TOOL_RESULT_BYTES:
            message = truncate_output_text(
                message, TruncationPolicy("bytes", max(0, MAX_RAW_TOOL_RESULT_BYTES - 64))
            )
        return ToolResult(
            call_id=call.id,
            tool_name=call.name,
            content=message,
            display_content=truncate_text(message, min(budget, 4_000)),
            is_error=True,
            dispatch_error=True,
            legacy_output_char_budget=spec.output_char_budget if spec is not None else None,
            is_tool_search_output=False,
        )


def _text(value: object, field: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError(f"{field} contains invalid Unicode") from exc


def _exception_message(exc: Exception) -> str:
    try:
        text = str(exc)
    except Exception:  # noqa: BLE001 - an extension's broken __str__ is still a call error
        text = "exception message unavailable"
    return f"{type(exc).__name__}: {text}".encode("utf-8", errors="replace").decode("utf-8")


def _json_equal(value: object, other: object) -> bool:
    """Compare enum values without Python's boolean/numeric coercion."""
    if isinstance(value, bool) or isinstance(other, bool):
        return type(value) is type(other) and value == other
    if isinstance(value, Mapping) or isinstance(other, Mapping):
        return (
            isinstance(value, Mapping)
            and isinstance(other, Mapping)
            and value.keys() == other.keys()
            and all(_json_equal(item, other[key]) for key, item in value.items())
        )
    if isinstance(value, list) or isinstance(other, list):
        return (
            isinstance(value, list)
            and isinstance(other, list)
            and len(value) == len(other)
            and all(_json_equal(a, b) for a, b in zip(value, other, strict=True))
        )
    return value == other


def _validate(value: object, schema: Mapping[str, Any], *, path: str) -> None:
    """Validate the JSON-Schema subset used by Corki's built-in tools."""

    if "anyOf" in schema:
        for branch in schema["anyOf"]:
            try:
                _validate(value, branch, path=path)
            except ValueError:
                continue
            break
        else:
            raise ValueError(f"{path} must match an allowed schema alternative")
    expected = schema.get("type")
    type_map: dict[str, type | tuple[type, ...]] = {
        "object": Mapping,
        "array": list,
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "null": type(None),
    }
    expected_types = [expected] if isinstance(expected, str) else expected
    if "type" in schema:
        if (
            not isinstance(expected_types, list)
            or not expected_types
            or any(not isinstance(kind, str) or kind not in type_map for kind in expected_types)
        ):
            raise ValueError(f"{path} has an unsupported type declaration")
        if not any(
            isinstance(value, type_map[kind])
            and not (kind in {"integer", "number"} and isinstance(value, bool))
            for kind in expected_types
        ):
            raise ValueError(f"{path} must be {' or '.join(expected_types)}")
    if "enum" in schema and not any(_json_equal(value, option) for option in schema["enum"]):
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
    if isinstance(value, list):
        if isinstance(schema.get("items"), Mapping):
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
