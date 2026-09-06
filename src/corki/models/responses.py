"""Streaming OpenAI Responses API adapter over canonical conversation items."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any

import httpx

from corki.models.base import ModelError, ModelErrorKind
from corki.models.capabilities import ProviderCapabilities, StructuredOutputProtocol
from corki.models.openai_compatible import (
    _check_response_limit,
    _decode_stream_object,
    _http_error,
    _protocol_error,
    _usage_integer,
)
from corki.models.tool_search import (
    native_search_item,
    native_tool_definition,
    response_tool_name,
    search_call_arguments,
)
from corki.models.types import (
    ModelCompleted,
    ModelEvent,
    ModelReasoningDelta,
    ModelRequest,
    ModelRetrying,
    ModelTextDelta,
    ModelUsage,
)
from corki.protocol.ids import ToolCallId, new_tool_call_id
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    ContextItem,
    ConversationItem,
    ReasoningItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
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
        max_retries: int = 3,
        retry_base_seconds: float = 0.5,
        response_char_limit: int = 4_000_000,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._capabilities = capabilities
        self._reasoning_effort = reasoning_effort
        self._max_retries = max_retries
        self._retry_base_seconds = retry_base_seconds
        self._response_char_limit = response_char_limit
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

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
        attempt = 0
        while True:
            emitted_data = False
            try:
                async with aclosing(self._stream_once(request, payload, headers)) as response:
                    async for event in response:
                        if isinstance(event, (ModelTextDelta, ModelReasoningDelta)):
                            emitted_data = True
                        yield event
                return
            except ModelError as exc:
                if emitted_data or not exc.retryable or attempt >= self._max_retries:
                    raise
                attempt += 1
                delay = exc.retry_after_seconds
                if delay is None:
                    delay = min(self._retry_base_seconds * (2 ** (attempt - 1)), 8.0)
                yield ModelRetrying(attempt, self._max_retries, delay, str(exc))
                await asyncio.sleep(delay)

    def _build_payload(self, request: ModelRequest) -> dict[str, Any]:
        native = request.tool_search_mode == "native"
        if native and not self._capabilities.supports_native_tool_search:
            raise ModelError("provider/model has not enabled native tool search capability")
        tools = [
            native_tool_definition(tool)
            if native
            else {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": dict(tool.parameters),
                "strict": False,
            }
            for tool in request.tools
            if tool.exposure in {ToolExposure.DIRECT, ToolExposure.DIRECT_MODEL_ONLY}
        ]
        payload: dict[str, Any] = {
            "model": request.model,
            "instructions": request.instructions,
            "input": [
                _to_response_input(item, native_search=native)
                for item in (*request.context_items, *request.items)
            ],
            "stream": True,
        }
        if tools and self._capabilities.supports_tools:
            payload["tools"] = tools
            payload["parallel_tool_calls"] = self._capabilities.supports_parallel_tools
        if self._reasoning_effort and self._capabilities.supports_reasoning_effort:
            payload["reasoning"] = {"effort": self._reasoning_effort, "summary": "auto"}
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
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        calls: dict[str, _FunctionBuffer] = {}
        usage = ModelUsage()
        metadata: dict[str, Any] = {}
        end_turn: bool | None = None
        reasoning_item_id: str | None = None
        encrypted_reasoning: str | None = None
        terminal = False
        total_chars = 0
        try:
            async with self._client.stream(
                "POST", f"{self._base_url}/responses", headers=headers, json=payload
            ) as response:
                if response.is_error:
                    body = (await response.aread()).decode(errors="replace")[:4_000]
                    raise _http_error(response, body)
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    event = _decode_stream_object(
                        data, "provider returned invalid Responses streaming JSON"
                    )
                    event_type = str(event.get("type", ""))
                    if event_type == "error":
                        error = event.get("error") or {}
                        if not isinstance(error, dict):
                            raise _protocol_error("Responses error payload is not an object")
                        raise ModelError(
                            str(error.get("message") or "Responses stream failed"),
                            kind=ModelErrorKind.SERVER,
                            retryable=not (text_parts or reasoning_parts or calls),
                        )
                    if event_type in {"response.failed", "response.incomplete"}:
                        raw_response = event.get("response") or {}
                        if not isinstance(raw_response, dict):
                            raise _protocol_error("Responses response payload is not an object")
                        raw_error = raw_response.get("error") or {}
                        details = raw_response.get("incomplete_details") or {}
                        if not isinstance(raw_error, dict) or not isinstance(details, dict):
                            raise _protocol_error("Responses failure details are invalid")
                        reason = str(
                            raw_error.get("message") or details.get("reason") or event_type
                        )
                        kind = (
                            ModelErrorKind.CONTEXT_WINDOW
                            if "token" in reason or "max_output" in reason
                            else ModelErrorKind.SERVER
                        )
                        raise ModelError(
                            f"Responses request did not complete: {reason}",
                            kind=kind,
                            retryable=(
                                kind is ModelErrorKind.SERVER
                                and not (text_parts or reasoning_parts or calls)
                            ),
                        )
                    if event_type == "response.output_text.delta":
                        delta = str(event.get("delta") or "")
                        if delta:
                            total_chars += len(delta)
                            _check_response_limit(total_chars, self._response_char_limit)
                            text_parts.append(delta)
                            yield ModelTextDelta(delta)
                    elif event_type in {
                        "response.reasoning_summary_text.delta",
                        "response.reasoning_text.delta",
                    }:
                        delta = str(event.get("delta") or "")
                        if delta:
                            total_chars += len(delta)
                            _check_response_limit(total_chars, self._response_char_limit)
                            reasoning_parts.append(delta)
                            yield ModelReasoningDelta(delta)
                    elif event_type == "response.output_item.added":
                        item = event.get("item") or {}
                        if not isinstance(item, dict):
                            raise _protocol_error("Responses output item is not an object")
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
                                _check_response_limit(total_chars, self._response_char_limit)
                                buffer.arguments = initial_arguments
                    elif event_type == "response.function_call_arguments.delta":
                        key = str(event.get("item_id") or event.get("output_index") or "0")
                        delta = str(event.get("delta") or "")
                        total_chars += len(delta)
                        _check_response_limit(total_chars, self._response_char_limit)
                        calls.setdefault(key, _FunctionBuffer()).arguments += delta
                    elif event_type == "response.output_item.done":
                        item = event.get("item") or {}
                        if not isinstance(item, dict):
                            raise _protocol_error("Responses output item is not an object")
                        if item.get("type") == "tool_search_call":
                            call_id, arguments = search_call_arguments(item)
                            key = str(item.get("id") or call_id)
                            total_chars += len(arguments)
                            _check_response_limit(total_chars, self._response_char_limit)
                            calls[key] = _FunctionBuffer(call_id, "tool_search", arguments)
                        elif item.get("type") == "function_call":
                            key = str(item.get("id") or event.get("output_index") or len(calls))
                            buffer = calls.setdefault(key, _FunctionBuffer())
                            buffer.call_id = str(item.get("call_id") or buffer.call_id)
                            buffer.name = response_tool_name(item, buffer.name)
                            if item.get("arguments") is not None:
                                arguments = str(item["arguments"])
                                total_chars += max(0, len(arguments) - len(buffer.arguments))
                                _check_response_limit(total_chars, self._response_char_limit)
                                buffer.arguments = arguments
                        elif item.get("type") == "reasoning":
                            reasoning_item_id = str(item.get("id") or "") or None
                            encrypted = item.get("encrypted_content")
                            if encrypted is not None:
                                encrypted = str(encrypted)
                                total_chars += max(
                                    0, len(encrypted) - len(encrypted_reasoning or "")
                                )
                                _check_response_limit(total_chars, self._response_char_limit)
                                encrypted_reasoning = encrypted
                            if not reasoning_parts:
                                summaries = _reasoning_summary(item)
                                total_chars += sum(map(len, summaries))
                                _check_response_limit(total_chars, self._response_char_limit)
                                reasoning_parts.extend(summaries)
                    elif event_type == "response.completed":
                        terminal = True
                        raw_response = event.get("response")
                        if not isinstance(raw_response, dict):
                            raise _protocol_error("Responses completion is not an object")
                        if not isinstance(raw_response.get("id"), str):
                            raise _protocol_error("Responses completion requires a string id")
                        end_turn = raw_response.get("end_turn")
                        if end_turn is not None and not isinstance(end_turn, bool):
                            raise _protocol_error("Responses end_turn must be a boolean or null")
                        if raw_response.get("id"):
                            metadata["response_id"] = raw_response["id"]
                        if raw_response.get("model"):
                            metadata["model"] = raw_response["model"]
                        if raw_response.get("usage"):
                            usage = _parse_responses_usage(raw_response["usage"])
                        output = raw_response.get("output") or []
                        if not isinstance(output, list):
                            raise _protocol_error("Responses output is not a list")
                        for output_item in output:
                            if not isinstance(output_item, dict):
                                raise _protocol_error("Responses output contains a non-object item")
                            if output_item.get("type") == "message" and not text_parts:
                                fallback = _message_output_text(output_item)
                                if fallback:
                                    total_chars += len(fallback)
                                    _check_response_limit(total_chars, self._response_char_limit)
                                    text_parts.append(fallback)
                                    yield ModelTextDelta(fallback)
                            elif output_item.get("type") == "tool_search_call":
                                call_id, arguments = search_call_arguments(output_item)
                                key = str(output_item.get("id") or call_id)
                                if key not in calls:
                                    total_chars += len(arguments)
                                    _check_response_limit(total_chars, self._response_char_limit)
                                    calls[key] = _FunctionBuffer(call_id, "tool_search", arguments)
                            elif output_item.get("type") == "function_call":
                                key = str(output_item.get("id") or len(calls))
                                if key not in calls:
                                    arguments = str(output_item.get("arguments") or "")
                                    total_chars += len(arguments)
                                    _check_response_limit(total_chars, self._response_char_limit)
                                    calls[key] = _FunctionBuffer(
                                        call_id=str(output_item.get("call_id") or ""),
                                        name=response_tool_name(output_item),
                                        arguments=arguments,
                                    )
                            elif output_item.get("type") == "reasoning":
                                reasoning_item_id = (
                                    str(output_item.get("id") or "") or reasoning_item_id
                                )
                                encrypted = output_item.get("encrypted_content")
                                if encrypted is not None:
                                    encrypted = str(encrypted)
                                    total_chars += max(
                                        0,
                                        len(encrypted) - len(encrypted_reasoning or ""),
                                    )
                                    _check_response_limit(total_chars, self._response_char_limit)
                                    encrypted_reasoning = encrypted
                                if not reasoning_parts:
                                    summaries = _reasoning_summary(output_item)
                                    total_chars += sum(map(len, summaries))
                                    _check_response_limit(total_chars, self._response_char_limit)
                                    reasoning_parts.extend(summaries)
                        # A completion event is authoritative. Do not wait for
                        # the HTTP peer to close an otherwise complete stream.
                        break
        except httpx.HTTPError as exc:
            raise ModelError(
                f"model transport failed: {exc}",
                kind=ModelErrorKind.TRANSPORT,
                retryable=True,
            ) from exc

        if not terminal:
            raise ModelError(
                "Responses stream closed before response.completed",
                kind=ModelErrorKind.PROTOCOL,
                retryable=not (text_parts or reasoning_parts or calls),
            )
        step_id = new_step_id()
        turn_id = request.items[-1].turn_id
        items: list[ConversationItem] = []
        if reasoning_parts:
            items.append(
                ReasoningItem(
                    "".join(reasoning_parts),
                    turn_id,
                    step_id,
                    provider_name=self._capabilities.name,
                    provider_item_id=reasoning_item_id,
                    encrypted_content=encrypted_reasoning,
                )
            )
        if text_parts:
            items.append(AssistantMessageItem("".join(text_parts), turn_id, step_id))
        items.extend(
            ToolCallItem(_finish_function_call(buffer), turn_id, step_id)
            for buffer in calls.values()
        )
        yield ModelCompleted(
            tuple(items), usage=usage, provider_metadata=metadata, end_turn=end_turn
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _to_response_input(item: ConversationItem, *, native_search: bool = False) -> dict[str, Any]:
    if native_search:
        native = native_search_item(item)
        if native is not None:
            return native
    if isinstance(item, UserMessageItem):
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
        return {
            "type": "function_call",
            "call_id": str(item.call.id),
            "name": item.call.name,
            "arguments": item.call.raw_arguments
            or json.dumps(dict(item.call.arguments or {}), ensure_ascii=False),
        }
    if isinstance(item, ToolResultItem):
        output: object = item.content
        if item.attachments:
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
            "type": "function_call_output",
            "call_id": str(item.call_id),
            "output": output,
        }
    if isinstance(item, ContextItem):
        return {"role": item.role.value, "content": item.content}
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
        input_tokens=_usage_integer(value.get("input_tokens") or 0, "input tokens"),
        output_tokens=_usage_integer(value.get("output_tokens") or 0, "output tokens"),
        cached_tokens=_usage_integer(input_details.get("cached_tokens") or 0, "cached tokens"),
        reasoning_tokens=_usage_integer(
            output_details.get("reasoning_tokens") or 0, "reasoning tokens"
        ),
    )


def _reasoning_summary(item: dict[str, Any]) -> list[str]:
    summary = item.get("summary") or []
    if not isinstance(summary, list):
        raise _protocol_error("Responses reasoning summary is not a list")
    return [
        str(part.get("text") or "")
        for part in summary
        if isinstance(part, dict) and part.get("text")
    ]


def _message_output_text(item: dict[str, Any]) -> str:
    content = item.get("content") or []
    if not isinstance(content, list):
        raise _protocol_error("Responses message content is not a list")
    return "".join(
        str(part.get("text") or "")
        for part in content
        if isinstance(part, dict) and part.get("type") in {"output_text", "text"}
    )
