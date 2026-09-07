"""Streaming OpenAI Responses API adapter over canonical conversation items."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any

import httpx

from corki.models.backoff import backoff, retry_limit
from corki.models.base import ModelError, ModelErrorKind
from corki.models.capabilities import ProviderCapabilities, StructuredOutputProtocol
from corki.models.freeform import CustomCalls, compatible_arguments, compatible_call
from corki.models.hosted_items import HostedItems
from corki.models.http_stream import model_http_stream
from corki.models.media import response_content
from corki.models.namespaces import group_tool_definitions, request_tool_aliases
from corki.models.openai_compatible import (
    _check_response_limit,
    _decode_stream_object,
    _protocol_error,
    _usage_integer,
)
from corki.models.response_content import ResponseContent
from corki.models.response_errors import failed_response, incomplete_response
from corki.models.tool_search import (
    native_search_item,
    response_tool_name,
    search_call_arguments,
)
from corki.models.types import (
    ModelCompleted,
    ModelEvent,
    ModelItemCompleted,
    ModelReasoningDelta,
    ModelRequest,
    ModelRetrying,
    ModelTextDelta,
    ModelUsage,
)
from corki.protocol.hosted import decode_hosted_payload, is_hosted_tool_payload
from corki.protocol.ids import ToolCallId, new_tool_call_id
from corki.protocol.items import (
    AssistantMessageItem,
    BudgetNoticeItem,
    CompactionItem,
    ContextItem,
    ConversationItem,
    HostedToolItem,
    ReasoningItem,
    ToolCallItem,
    ToolResultItem,
    TurnAbortedItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tool_names import response_call_name
from corki.protocol.tools import ToolCall, ToolExposure


@dataclass(slots=True)
class _FunctionBuffer:
    call_id: str = ""
    name: str = ""
    arguments: str = ""


class OpenAIResponsesModel:
    """Translate Corki's item protocol to/from the Responses streaming API."""

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str,
        capabilities: ProviderCapabilities,
        timeout_seconds: float = 120.0,
        reasoning_effort: str | None = None,
        max_retries: int = 5,
        request_max_retries: int = 4,
        retry_base_seconds: float = 0.2,
        response_char_limit: int = 4_000_000,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._capabilities = capabilities
        self._reasoning_effort = reasoning_effort
        self._max_retries = retry_limit(max_retries)
        self._request_max_retries = retry_limit(request_max_retries)
        self._retry_base_seconds = retry_base_seconds
        self._response_char_limit = response_char_limit
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None
        self._server_reasoning_included = False

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        if not self._api_key:
            raise ModelError(
                "No API key configured. Set CORKI_API_KEY or OPENAI_API_KEY.",
                kind=ModelErrorKind.AUTHENTICATION,
            )
        if not request.items:
            raise ModelError(
                "model request contains no conversation items",
                kind=ModelErrorKind.PROTOCOL,
            )
        payload = self._build_payload(request)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        for key in ("x-codex-window-id", "x-codex-turn-metadata"):
            if request.client_metadata and key in request.client_metadata:
                headers[key] = request.client_metadata[key]
        attempt = 0
        while True:
            emitted_data = False
            try:
                async with aclosing(self._stream_once(request, payload, headers)) as response:
                    async for event in response:
                        if isinstance(
                            event, (ModelTextDelta, ModelReasoningDelta, ModelItemCompleted)
                        ):
                            emitted_data = True
                        yield event
                return
            except ModelError as exc:
                if (
                    request.harness_managed_retries
                    or emitted_data
                    or not exc.retryable
                    or attempt >= self._max_retries
                ):
                    raise
                attempt += 1
                delay = exc.retry_after_seconds
                if delay is None:
                    delay = backoff(self._retry_base_seconds, attempt - 1)
                yield ModelRetrying(attempt, self._max_retries, delay, str(exc))
                await asyncio.sleep(delay)

    def _build_payload(self, request: ModelRequest) -> dict[str, Any]:
        request_tool_aliases(request)
        native_namespaces = request.tool_namespace_mode == "native"
        if native_namespaces and not self._capabilities.supports_native_namespaces:
            raise ModelError("provider/model has not enabled native namespace capability")
        native = request.tool_search_mode == "native"
        if native and not self._capabilities.supports_native_tool_search:
            raise ModelError("provider/model has not enabled native tool search capability")
        native_freeform = request.tool_freeform_mode == "native"
        if native_freeform and not self._capabilities.supports_native_freeform:
            raise ModelError("provider/model has not enabled native freeform capability")
        tools = group_tool_definitions(
            [
                tool
                for tool in request.tools
                if tool.exposure in {ToolExposure.DIRECT, ToolExposure.DIRECT_MODEL_ONLY}
            ],
            native_freeform=native_freeform,
            native_search=native,
            native_namespaces=native_namespaces,
        )
        payload: dict[str, Any] = {
            "model": request.model,
            "instructions": request.instructions,
            "input": [
                _to_response_input(
                    item,
                    native_search=native,
                    native_freeform=native_freeform,
                    native_namespaces=native_namespaces,
                    audio_enabled=self._capabilities.supports_audio_input,
                    encrypted_enabled=self._capabilities.supports_encrypted_tool_output,
                )
                for item in (*request.context_items, *request.items)
                if not (isinstance(item, ContextItem) and item.is_snapshot_only)
            ],
            "stream": True,
        }
        if request.client_metadata is not None:
            payload["client_metadata"] = dict(request.client_metadata)
        if tools and self._capabilities.supports_tools:
            payload["tools"] = tools
            payload["parallel_tool_calls"] = self._capabilities.supports_parallel_tools
        effort = (
            request.reasoning_effort
            if request.reasoning_effort is not None
            else self._reasoning_effort
        )
        if effort and self._capabilities.supports_reasoning_effort:
            payload["reasoning"] = {"effort": effort, "summary": "auto"}
            payload["include"] = ["reasoning.encrypted_content"]
        if (
            request.output_schema is not None
            and self._capabilities.structured_output_protocol
            is StructuredOutputProtocol.JSON_SCHEMA
        ):
            payload["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": request.output_schema_name,
                    "strict": True,
                    "schema": dict(request.output_schema),
                }
            }
        return payload

    async def _stream_once(
        self,
        request: ModelRequest,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> AsyncIterator[ModelEvent]:
        calls: dict[str, _FunctionBuffer] = {}
        finished_calls: dict[str, ToolCallItem] = {}
        finished_items: list[ConversationItem] = []
        step_id = new_step_id()
        turn_id = request.items[-1].turn_id
        content = ResponseContent(turn_id, step_id, self._capabilities.name)
        hosted = HostedItems(turn_id, step_id)
        custom = CustomCalls(
            turn_id,
            step_id,
            name_aliases=request_tool_aliases(request)
            if request.tool_namespace_mode != "native"
            else None,
        )

        def finish_call(buffer: _FunctionBuffer) -> ToolCall:
            return compatible_call(_finish_function_call(buffer), request)

        def require_custom() -> None:
            if request.tool_freeform_mode != "native":
                raise _protocol_error("provider returned native custom input in compatible mode")

        usage = ModelUsage()
        metadata: dict[str, Any] = {}
        end_turn: bool | None = None
        terminal = False
        response_error: ModelError | None = None
        total_chars = 0
        try:
            async with model_http_stream(
                self._client,
                f"{self._base_url}/responses",
                headers=headers,
                payload=payload,
                max_retries=self._request_max_retries,
                base_seconds=self._retry_base_seconds,
            ) as response:
                if "x-reasoning-included" in response.headers:
                    self._server_reasoning_included = True
                if self._server_reasoning_included:
                    metadata["server_reasoning_included"] = True
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        event = _decode_stream_object(
                            data, "provider returned invalid Responses streaming JSON"
                        )
                    except ModelError:
                        # Codex's SSE parser skips undecodable envelopes. Do not
                        # log payload contents; a valid terminal is still required.
                        logging.getLogger(__name__).debug(
                            "Ignoring undecodable Responses SSE event"
                        )
                        continue
                    event_type = str(event.get("type", ""))
                    if event_type in {"response.failed", "response.incomplete"}:
                        response_error = (
                            failed_response(event.get("response"))
                            if event_type == "response.failed"
                            else incomplete_response(event.get("response"))
                        )
                        continue
                    if event_type == "response.output_text.delta":
                        delta = content.delta(event, reasoning=False)
                        _check_response_limit(
                            total_chars + content.extra_chars, self._response_char_limit
                        )
                        if delta.delta:
                            yield delta
                    elif event_type in {
                        "response.reasoning_summary_text.delta",
                        "response.reasoning_text.delta",
                    }:
                        delta = content.delta(event, reasoning=True)
                        _check_response_limit(
                            total_chars + content.extra_chars, self._response_char_limit
                        )
                        if delta.delta:
                            yield delta
                    elif event_type == "response.output_item.added":
                        item = event.get("item") or {}
                        if not isinstance(item, dict):
                            raise _protocol_error("Responses output item is not an object")
                        if item.get("type") in {"message", "reasoning"}:
                            content.added(item, event)
                        if item.get("type") == "custom_tool_call":
                            require_custom()
                            before = custom.chars
                            custom.added(item, event)
                            total_chars += custom.chars - before
                            _check_response_limit(
                                total_chars + content.extra_chars, self._response_char_limit
                            )
                        if item.get("type") == "function_call":
                            key = str(item.get("id") or event.get("output_index") or len(calls))
                            buffer = calls.setdefault(key, _FunctionBuffer())
                            buffer.call_id = str(item.get("call_id") or buffer.call_id)
                            buffer.name = response_tool_name(item, buffer.name)
                            initial_arguments = str(item.get("arguments") or "")
                            if initial_arguments:
                                total_chars += max(
                                    0, len(initial_arguments) - len(buffer.arguments)
                                )
                                _check_response_limit(
                                    total_chars + content.extra_chars, self._response_char_limit
                                )
                                buffer.arguments = initial_arguments
                    elif event_type == "response.custom_tool_call_input.delta":
                        require_custom()
                        before = custom.chars
                        custom.delta(event)
                        total_chars += custom.chars - before
                        _check_response_limit(
                            total_chars + content.extra_chars, self._response_char_limit
                        )
                    elif event_type == "response.function_call_arguments.delta":
                        key = str(event.get("item_id") or event.get("output_index") or "0")
                        if key in finished_calls:
                            raise _protocol_error("arguments delta after completed tool item")
                        delta = str(event.get("delta") or "")
                        total_chars += len(delta)
                        _check_response_limit(
                            total_chars + content.extra_chars, self._response_char_limit
                        )
                        calls.setdefault(key, _FunctionBuffer()).arguments += delta
                    elif event_type == "response.output_item.done":
                        item = event.get("item") or {}
                        if not isinstance(item, dict):
                            raise _protocol_error("Responses output item is not an object")
                        if is_hosted_tool_payload(item):
                            before = hosted.chars
                            complete_item = hosted.complete(item, event)
                            total_chars += hosted.chars - before
                            _check_response_limit(
                                total_chars + content.extra_chars, self._response_char_limit
                            )
                            if complete_item is not None:
                                finished_items.append(complete_item)
                                yield ModelItemCompleted(complete_item)
                            continue
                        if item.get("type") == "custom_tool_call":
                            require_custom()
                            before = custom.chars
                            complete_item = custom.complete(item, event)
                            total_chars += custom.chars - before
                            _check_response_limit(
                                total_chars + content.extra_chars, self._response_char_limit
                            )
                            if complete_item is not None:
                                finished_items.append(complete_item)
                                yield ModelItemCompleted(complete_item)
                        elif item.get("type") == "tool_search_call":
                            call_id, arguments = search_call_arguments(item)
                            key = str(item.get("id") or call_id)
                            total_chars += len(arguments)
                            _check_response_limit(
                                total_chars + content.extra_chars, self._response_char_limit
                            )
                            calls[key] = _FunctionBuffer(call_id, "tool_search", arguments)
                        elif item.get("type") == "function_call":
                            key = str(item.get("id") or event.get("output_index") or len(calls))
                            buffer = calls.setdefault(key, _FunctionBuffer())
                            buffer.call_id = str(item.get("call_id") or buffer.call_id)
                            buffer.name = response_tool_name(item, buffer.name)
                            if item.get("arguments") is not None:
                                arguments = str(item["arguments"])
                                total_chars += max(0, len(arguments) - len(buffer.arguments))
                                _check_response_limit(
                                    total_chars + content.extra_chars, self._response_char_limit
                                )
                                buffer.arguments = arguments
                        elif item.get("type") in {"message", "reasoning"}:
                            complete_item = content.complete(item, event)
                            _check_response_limit(
                                total_chars + content.extra_chars, self._response_char_limit
                            )
                            if complete_item is not None:
                                finished_items.append(complete_item)
                                yield ModelItemCompleted(complete_item)
                        if item.get("type") in {"function_call", "tool_search_call"}:
                            call = finish_call(calls[key])
                            if key in finished_calls:
                                if finished_calls[key].call != call or finished_calls[
                                    key
                                ].contains_external_context != (
                                    item.get("type") == "tool_search_call"
                                ):
                                    raise _protocol_error("completed tool item changed")
                            else:
                                finished_calls[key] = ToolCallItem(
                                    call,
                                    turn_id,
                                    step_id,
                                    contains_external_context=item.get("type")
                                    == "tool_search_call",
                                )
                                finished_items.append(finished_calls[key])
                                yield ModelItemCompleted(finished_calls[key])
                    elif event_type == "response.completed":
                        raw_response = event.get("response")
                        try:
                            if not isinstance(raw_response, dict):
                                raise _completion_error("Responses completion is not an object")
                            if not isinstance(raw_response.get("id"), str):
                                raise _completion_error("Responses completion requires a string id")
                            end_turn = raw_response.get("end_turn")
                            if end_turn is not None and not isinstance(end_turn, bool):
                                raise _completion_error(
                                    "Responses end_turn must be a boolean or null"
                                )
                            if raw_response.get("usage"):
                                usage = _parse_responses_usage(raw_response["usage"])
                        except ModelError as error:
                            response_error = _completion_error(str(error))
                            continue
                        terminal = True
                        if raw_response.get("id"):
                            metadata["response_id"] = raw_response["id"]
                        if raw_response.get("model"):
                            metadata["model"] = raw_response["model"]
                        output = raw_response.get("output") or []
                        if not isinstance(output, list):
                            raise _protocol_error("Responses output is not a list")
                        for output_index, output_item in enumerate(output):
                            if not isinstance(output_item, dict):
                                raise _protocol_error("Responses output contains a non-object item")
                            if is_hosted_tool_payload(output_item):
                                before = hosted.chars
                                complete_item = hosted.complete(
                                    output_item, {"output_index": output_index}
                                )
                                total_chars += hosted.chars - before
                                _check_response_limit(
                                    total_chars + content.extra_chars, self._response_char_limit
                                )
                                if complete_item is not None:
                                    finished_items.append(complete_item)
                                    yield ModelItemCompleted(complete_item)
                                continue
                            if output_item.get("type") == "custom_tool_call":
                                require_custom()
                                before = custom.chars
                                complete_item = custom.complete(
                                    output_item, {"output_index": output_index}
                                )
                                total_chars += custom.chars - before
                                _check_response_limit(
                                    total_chars + content.extra_chars, self._response_char_limit
                                )
                                if complete_item is not None:
                                    finished_items.append(complete_item)
                                    yield ModelItemCompleted(complete_item)
                            elif output_item.get("type") in {"message", "reasoning"}:
                                complete_item = content.complete(
                                    output_item, {"output_index": output_index}
                                )
                                _check_response_limit(
                                    total_chars + content.extra_chars, self._response_char_limit
                                )
                                if complete_item is not None:
                                    finished_items.append(complete_item)
                                    yield ModelItemCompleted(complete_item)
                            elif output_item.get("type") == "tool_search_call":
                                call_id, arguments = search_call_arguments(output_item)
                                key = str(output_item.get("id") or call_id)
                                if key in finished_calls and finished_calls[
                                    key
                                ].call != finish_call(
                                    _FunctionBuffer(call_id, "tool_search", arguments)
                                ):
                                    raise _protocol_error("completed response changed a tool item")
                                if key not in calls:
                                    total_chars += len(arguments)
                                    _check_response_limit(
                                        total_chars + content.extra_chars, self._response_char_limit
                                    )
                                    calls[key] = _FunctionBuffer(call_id, "tool_search", arguments)
                            elif output_item.get("type") == "function_call":
                                key = str(
                                    output_item.get("id")
                                    or next(
                                        (
                                            key
                                            for key, buffer in calls.items()
                                            if buffer.call_id == output_item.get("call_id")
                                        ),
                                        len(calls),
                                    )
                                )
                                if key in finished_calls and finished_calls[
                                    key
                                ].call != finish_call(
                                    _FunctionBuffer(
                                        str(output_item.get("call_id") or ""),
                                        response_tool_name(output_item),
                                        str(output_item.get("arguments") or ""),
                                    )
                                ):
                                    raise _protocol_error("completed response changed a tool item")
                                if key not in calls:
                                    arguments = str(output_item.get("arguments") or "")
                                    total_chars += len(arguments)
                                    _check_response_limit(
                                        total_chars + content.extra_chars, self._response_char_limit
                                    )
                                    calls[key] = _FunctionBuffer(
                                        call_id=str(output_item.get("call_id") or ""),
                                        name=response_tool_name(output_item),
                                        arguments=arguments,
                                    )
                            if (
                                output_item.get("type") in {"function_call", "tool_search_call"}
                                and key in finished_calls
                                and finished_calls[key].contains_external_context
                                != (output_item.get("type") == "tool_search_call")
                            ):
                                raise _protocol_error("completed response changed tool item kind")
                            if (
                                output_item.get("type") in {"function_call", "tool_search_call"}
                                and key not in finished_calls
                            ):
                                finished_calls[key] = ToolCallItem(
                                    finish_call(calls[key]),
                                    turn_id,
                                    step_id,
                                    contains_external_context=output_item.get("type")
                                    == "tool_search_call",
                                )
                                finished_items.append(finished_calls[key])
                                yield ModelItemCompleted(finished_calls[key])
                        # A completion event is authoritative. Do not wait for
                        # the HTTP peer to close an otherwise complete stream.
                        break
        except httpx.HTTPError as exc:
            raise ModelError(
                f"model transport failed: {exc}",
                kind=(
                    ModelErrorKind.CONNECTION
                    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))
                    else ModelErrorKind.TRANSPORT
                ),
                retryable=True,
            ) from exc

        if not terminal:
            if response_error is not None:
                raise response_error
            raise ModelError(
                "Responses stream closed before response.completed",
                kind=ModelErrorKind.PROTOCOL,
                retryable=True,
            )
        for pending in content.finish_pending():
            if pending is not None:
                finished_items.append(pending)
                yield ModelItemCompleted(pending)
        finished_items.extend(
            ToolCallItem(finish_call(buffer), turn_id, step_id)
            for key, buffer in calls.items()
            if key not in finished_calls
        )
        yield ModelCompleted(
            tuple(finished_items), usage=usage, provider_metadata=metadata, end_turn=end_turn
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _completion_error(message: str) -> ModelError:
    return ModelError(message, kind=ModelErrorKind.PROTOCOL, retryable=True)


def _to_response_input(
    item: ConversationItem,
    *,
    native_search: bool = False,
    native_freeform: bool = False,
    native_namespaces: bool = False,
    audio_enabled: bool = False,
    encrypted_enabled: bool = False,
) -> dict[str, Any]:
    if isinstance(item, HostedToolItem):
        payload = decode_hosted_payload(item.visible_payload_json)
        if payload["type"] in {"tool_search_call", "tool_search_output"} and not native_search:
            return {"role": "user", "content": item.compatibility_content}
        return payload
    if native_search:
        native = native_search_item(
            item, native_freeform=native_freeform, native_namespaces=native_namespaces
        )
        if native is not None:
            return native
    if isinstance(item, UserMessageItem):
        if item.content_items:
            return {
                "role": "user",
                "content": response_content(item.content_items, audio_enabled=audio_enabled),
            }
        content: list[dict[str, Any]] = [{"type": "input_text", "text": item.content}]
        content.extend(
            {
                "type": "input_image",
                "image_url": attachment.data_url,
                "detail": attachment.detail,
            }
            for attachment in item.attachments
        )
        return {"role": "user", "content": content}
    if isinstance(item, AssistantMessageItem):
        return {
            "role": "assistant",
            "content": [{"type": "output_text", "text": item.content}],
            **({"phase": item.phase} if item.phase is not None else {}),
        }
    if isinstance(item, ReasoningItem):
        if item.encrypted_content:
            value: dict[str, Any] = {
                "type": "reasoning",
                "encrypted_content": item.encrypted_content,
                "summary": (
                    [{"type": "summary_text", "text": item.summary or item.content}]
                    if item.summary or item.content
                    else []
                ),
            }
            if item.provider_item_id:
                value["id"] = item.provider_item_id
            return value
        # A provider-neutral summary still carries useful replay context.
        return {"role": "developer", "content": item.summary or item.content}
    if isinstance(item, ToolCallItem):
        if native_freeform and item.call.input_kind == "freeform":
            return {
                "type": "custom_tool_call",
                "call_id": str(item.call.id),
                **response_call_name(item.call.name, native_namespaces=native_namespaces),
                "input": item.call.raw_arguments,
            }
        return {
            "type": "function_call",
            "call_id": str(item.call.id),
            **response_call_name(item.call.name, native_namespaces=native_namespaces),
            "arguments": compatible_arguments(item.call),
        }
    if isinstance(item, ToolResultItem):
        output: object = item.content
        if item.content_items:
            output = response_content(
                item.content_items, audio_enabled=audio_enabled, encrypted_enabled=encrypted_enabled
            )
        elif item.attachments:
            output = [
                {"type": "input_text", "text": item.content},
                *(
                    {
                        "type": "input_image",
                        "image_url": attachment.data_url,
                        "detail": attachment.detail,
                    }
                    for attachment in item.attachments
                ),
            ]
        return {
            "type": "custom_tool_call_output"
            if native_freeform and item.input_kind == "freeform"
            else "function_call_output",
            "call_id": str(item.call_id),
            "output": output,
        }
    if isinstance(item, ContextItem):
        return {"role": item.role.value, "content": item.content}
    if isinstance(item, TurnAbortedItem):
        return {"role": "user", "content": item.content}
    if isinstance(item, BudgetNoticeItem):
        return {"role": "developer", "content": item.content}
    if isinstance(item, CompactionItem):
        return {
            "role": "developer",
            "content": f"<compaction_summary>\n{item.summary}\n</compaction_summary>",
        }
    raise TypeError(f"unsupported Responses input item: {type(item).__name__}")


def _finish_function_call(buffer: _FunctionBuffer) -> ToolCall:
    raw = buffer.arguments or "{}"
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("tool arguments must be a JSON object")
        arguments, parse_error = parsed, None
    except (json.JSONDecodeError, ValueError) as exc:
        arguments, parse_error = None, str(exc)
    return ToolCall(
        ToolCallId(buffer.call_id) if buffer.call_id else new_tool_call_id(),
        buffer.name,
        arguments,
        raw_arguments=raw,
        parse_error=parse_error,
    )


def _parse_responses_usage(value: object) -> ModelUsage:
    if not isinstance(value, dict):
        raise _protocol_error("Responses token usage is not an object")
    input_details = value.get("input_tokens_details") or {}
    output_details = value.get("output_tokens_details") or {}
    if not isinstance(input_details, dict) or not isinstance(output_details, dict):
        raise _protocol_error("Responses token usage details are invalid")
    return ModelUsage(
        total_tokens=(
            _usage_integer(value["total_tokens"], "total tokens")
            if value.get("total_tokens") is not None
            else None
        ),
        input_tokens=_usage_integer(value.get("input_tokens") or 0, "input tokens"),
        output_tokens=_usage_integer(value.get("output_tokens") or 0, "output tokens"),
        cached_tokens=_usage_integer(input_details.get("cached_tokens") or 0, "cached tokens"),
        reasoning_tokens=_usage_integer(
            output_details.get("reasoning_tokens") or 0, "reasoning tokens"
        ),
    )
