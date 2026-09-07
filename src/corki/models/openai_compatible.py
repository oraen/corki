"""Reliable streaming Chat Completions transport.

This adapter is the only place that understands Chat Completions message
grouping. Corki's graph, persistence, and context layers exchange
provider-neutral conversation items.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterable
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any

import httpx

from corki.models.backoff import backoff, retry_limit
from corki.models.base import ModelError, ModelErrorKind
from corki.models.capabilities import ProviderCapabilities, StructuredOutputProtocol
from corki.models.freeform import compatible_arguments, compatible_call
from corki.models.http_stream import model_http_stream
from corki.models.media import chat_content
from corki.models.namespaces import request_tool_aliases
from corki.models.types import (
    ModelCompleted,
    ModelEvent,
    ModelReasoningDelta,
    ModelRequest,
    ModelRetrying,
    ModelTextDelta,
    ModelUsage,
)
from corki.protocol.ids import ModelStepId, ToolCallId, new_tool_call_id
from corki.protocol.items import (
    AssistantMessageItem,
    BudgetNoticeItem,
    CompactionItem,
    ContextItem,
    ContextRole,
    ConversationItem,
    HostedToolItem,
    ReasoningItem,
    ToolCallItem,
    ToolResultItem,
    TurnAbortedItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tool_names import compatible_tool_name
from corki.protocol.tools import TextContent, ToolCall, ToolExposure


@dataclass(slots=True)
class _ToolCallBuffer:
    id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass(slots=True)
class _AssistantBuffer:
    step_id: ModelStepId
    content: str = ""
    reasoning: str | None = None
    calls: list[ToolCallItem] | None = None


class OpenAICompatibleModel:
    """Call a capability-scoped OpenAI-compatible chat endpoint."""

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str,
        capabilities: ProviderCapabilities,
        timeout_seconds: float = 120.0,
        thinking_enabled: bool | None = None,
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
        self._thinking_enabled = thinking_enabled
        self._reasoning_effort = reasoning_effort
        self._max_retries = retry_limit(max_retries)
        self._request_max_retries = retry_limit(request_max_retries)
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
        if request.tool_namespace_mode == "native":
            raise ModelError("native namespaces are not supported by Chat Completions")
        if request.tool_search_mode == "native":
            raise ModelError("native tool search is not supported by Chat Completions")
        if request.tool_freeform_mode == "native":
            raise ModelError("native freeform is not supported by Chat Completions")
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": self._convert_items(request),
            "stream": True,
        }
        direct_tools = [
            tool.as_chat_completion_tool()
            for tool in request.tools
            if tool.exposure in {ToolExposure.DIRECT, ToolExposure.DIRECT_MODEL_ONLY}
        ]
        if direct_tools and self._capabilities.supports_tools:
            payload["tools"] = direct_tools
            payload["parallel_tool_calls"] = self._capabilities.supports_parallel_tools
        if self._capabilities.supports_stream_usage:
            payload["stream_options"] = {"include_usage": True}
        if self._thinking_enabled is not None and self._capabilities.supports_thinking_toggle:
            payload["thinking"] = {"type": "enabled" if self._thinking_enabled else "disabled"}
        effort = (
            request.reasoning_effort
            if request.reasoning_effort is not None
            else self._reasoning_effort
        )
        if effort is not None and self._capabilities.supports_reasoning_effort:
            payload["reasoning_effort"] = effort
        if request.output_schema is not None:
            if (
                self._capabilities.structured_output_protocol
                is StructuredOutputProtocol.JSON_SCHEMA
            ):
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": request.output_schema_name,
                        "strict": True,
                        "schema": dict(request.output_schema),
                    },
                }
            elif (
                self._capabilities.structured_output_protocol
                is StructuredOutputProtocol.JSON_OBJECT
            ):
                payload["response_format"] = {"type": "json_object"}
        return payload

    async def _stream_once(
        self,
        request: ModelRequest,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> AsyncIterator[ModelEvent]:
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        call_buffers: dict[int, _ToolCallBuffer] = {}
        usage = ModelUsage()
        provider_metadata: dict[str, Any] = {}
        saw_terminal = False
        total_chars = 0
        try:
            async with model_http_stream(
                self._client,
                f"{self._base_url}/chat/completions",
                headers=headers,
                payload=payload,
                max_retries=self._request_max_retries,
                base_seconds=self._retry_base_seconds,
            ) as response:
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data:
                        continue
                    if data == "[DONE]":
                        saw_terminal = True
                        break
                    packet = _decode_stream_object(data, "provider returned invalid streaming JSON")
                    if packet.get("error"):
                        raw_error = packet["error"]
                        message = (
                            raw_error.get("message", "provider stream failed")
                            if isinstance(raw_error, dict)
                            else str(raw_error)
                        )
                        raise ModelError(
                            message,
                            kind=ModelErrorKind.SERVER,
                            retryable=True,
                        )
                    if packet.get("id"):
                        provider_metadata["response_id"] = packet["id"]
                    if packet.get("model"):
                        provider_metadata["model"] = packet["model"]
                    if raw_usage := packet.get("usage"):
                        if not isinstance(raw_usage, dict):
                            raise _protocol_error("provider returned invalid usage data")
                        usage = _parse_usage(raw_usage)
                    choices = packet.get("choices") or []
                    if not isinstance(choices, list):
                        raise _protocol_error("provider returned invalid choices data")
                    if not choices:
                        continue
                    choice = choices[0]
                    if not isinstance(choice, dict):
                        raise _protocol_error("provider returned a non-object choice")
                    finish_reason = choice.get("finish_reason")
                    if finish_reason is not None:
                        if finish_reason == "length":
                            raise ModelError(
                                "model stopped because its output token limit was reached",
                                kind=ModelErrorKind.OUTPUT_LIMIT,
                            )
                        if finish_reason not in {"stop", "tool_calls", "function_call"}:
                            raise ModelError(
                                f"model stopped with finish reason: {finish_reason}",
                                kind=ModelErrorKind.PROTOCOL,
                            )
                        saw_terminal = True
                        provider_metadata["finish_reason"] = finish_reason
                    delta = choice.get("delta") or {}
                    if not isinstance(delta, dict):
                        raise _protocol_error("provider returned invalid delta data")
                    reasoning = _text_from_delta(
                        delta.get("reasoning_content", delta.get("reasoning"))
                    )
                    if reasoning:
                        total_chars += len(reasoning)
                        _check_response_limit(total_chars, self._response_char_limit)
                        reasoning_parts.append(reasoning)
                        yield ModelReasoningDelta(reasoning)
                    content = _text_from_delta(delta.get("content"))
                    if content:
                        total_chars += len(content)
                        _check_response_limit(total_chars, self._response_char_limit)
                        text_parts.append(content)
                        yield ModelTextDelta(content)
                    raw_calls = delta.get("tool_calls") or []
                    if not isinstance(raw_calls, list):
                        raise _protocol_error("provider returned invalid tool_calls data")
                    for raw_call in raw_calls:
                        if not isinstance(raw_call, dict):
                            raise _protocol_error("provider returned a non-object tool call")
                        try:
                            index = int(raw_call.get("index", 0))
                        except (TypeError, ValueError) as exc:
                            raise _protocol_error(
                                "provider returned an invalid tool call index"
                            ) from exc
                        if index < 0:
                            raise _protocol_error("provider returned a negative tool call index")
                        buffer = call_buffers.setdefault(index, _ToolCallBuffer())
                        if raw_call.get("id"):
                            call_id = str(raw_call["id"])
                            if buffer.id and buffer.id != call_id:
                                raise _protocol_error("provider changed a streaming tool call id")
                            buffer.id = call_id
                        function = raw_call.get("function") or {}
                        if not isinstance(function, dict):
                            raise _protocol_error(
                                "provider returned invalid tool call function data"
                            )
                        if function.get("name"):
                            name = str(function["name"])
                            total_chars += len(name)
                            _check_response_limit(total_chars, self._response_char_limit)
                            buffer.name += name
                        if function.get("arguments"):
                            arguments = str(function["arguments"])
                            total_chars += len(arguments)
                            _check_response_limit(total_chars, self._response_char_limit)
                            buffer.arguments += arguments
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

        if not saw_terminal:
            raise ModelError(
                "model stream closed before a completion marker",
                kind=ModelErrorKind.PROTOCOL,
                retryable=True,
            )

        step_id = new_step_id()
        if not request.items:
            raise ModelError(
                "model request contains no conversation items",
                kind=ModelErrorKind.PROTOCOL,
            )
        turn_id = request.items[-1].turn_id
        items: list[ConversationItem] = []
        reasoning = "".join(reasoning_parts)
        if reasoning:
            items.append(
                ReasoningItem(
                    reasoning,
                    turn_id,
                    step_id,
                    provider_name=self._capabilities.name,
                )
            )
        text = "".join(text_parts)
        if text:
            items.append(AssistantMessageItem(text, turn_id, step_id))
        items.extend(
            ToolCallItem(
                compatible_call(_finish_tool_call(call_buffers[index]), request), turn_id, step_id
            )
            for index in sorted(call_buffers)
        )
        yield ModelCompleted(tuple(items), usage=usage, provider_metadata=provider_metadata)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _convert_items(self, request: ModelRequest) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = [{"role": "system", "content": request.instructions}]
        assistant: _AssistantBuffer | None = None

        def flush_assistant() -> None:
            nonlocal assistant
            if assistant is None:
                return
            item: dict[str, Any] = {
                "role": "assistant",
                "content": assistant.content or None,
            }
            if assistant.calls:
                item["tool_calls"] = [
                    {
                        "id": str(call_item.call.id),
                        "type": "function",
                        "function": {
                            "name": compatible_tool_name(call_item.call.name),
                            "arguments": compatible_arguments(call_item.call),
                        },
                    }
                    for call_item in assistant.calls
                ]
            if self._thinking_enabled and self._capabilities.requires_reasoning_replay:
                item["reasoning_content"] = assistant.reasoning or " "
            converted.append(item)
            assistant = None

        for conversation_item in (*request.context_items, *request.items):
            if isinstance(conversation_item, ContextItem) and conversation_item.is_snapshot_only:
                continue
            step_id = getattr(conversation_item, "step_id", None)
            if isinstance(
                conversation_item,
                (AssistantMessageItem, ReasoningItem, ToolCallItem),
            ):
                if assistant is None or assistant.step_id != step_id:
                    flush_assistant()
                    assistant = _AssistantBuffer(step_id=step_id)
                if isinstance(conversation_item, AssistantMessageItem):
                    assistant.content += conversation_item.content
                elif isinstance(conversation_item, ReasoningItem):
                    assistant.reasoning = (assistant.reasoning or "") + conversation_item.content
                else:
                    if assistant.calls is None:
                        assistant.calls = []
                    assistant.calls.append(conversation_item)
                continue

            flush_assistant()
            if isinstance(conversation_item, HostedToolItem):
                converted.append(
                    {"role": "user", "content": conversation_item.compatibility_content}
                )
            elif isinstance(conversation_item, UserMessageItem):
                converted.append(
                    _user_message(
                        conversation_item, audio_enabled=self._capabilities.supports_audio_input
                    )
                )
            elif isinstance(conversation_item, ToolResultItem):
                parts = conversation_item.content_items
                if parts:
                    has_media = any(not isinstance(part, TextContent) for part in parts)
                    converted.append(
                        {
                            "role": "tool",
                            "tool_call_id": str(conversation_item.call_id),
                            "content": "Tool output follows in the next user content block."
                            if has_media
                            else conversation_item.content,
                        }
                    )
                    if has_media:
                        converted.append(
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": (
                                            f"Output from tool {conversation_item.tool_name}, "
                                            f"call {conversation_item.call_id}:"
                                        ),
                                    },
                                    *chat_content(
                                        parts, audio_enabled=self._capabilities.supports_audio_input
                                    ),
                                ],
                            }
                        )
                    continue
                converted.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(conversation_item.call_id),
                        "content": conversation_item.content,
                    }
                )
                converted.extend(_attachment_messages(conversation_item.attachments))
            elif isinstance(conversation_item, ContextItem):
                role = "system" if conversation_item.role is ContextRole.DEVELOPER else "user"
                converted.append({"role": role, "content": conversation_item.content})
            elif isinstance(conversation_item, TurnAbortedItem):
                converted.append({"role": "user", "content": conversation_item.content})
            elif isinstance(conversation_item, BudgetNoticeItem):
                converted.append({"role": "system", "content": conversation_item.content})
            elif isinstance(conversation_item, CompactionItem):
                converted.append(
                    {
                        "role": "system",
                        "content": (
                            "<compaction_summary>\n"
                            f"{conversation_item.summary}\n"
                            "</compaction_summary>"
                        ),
                    }
                )
        flush_assistant()
        return converted


def _user_message(item: UserMessageItem, *, audio_enabled=False) -> dict[str, Any]:
    if item.content_items:
        return {
            "role": "user",
            "content": chat_content(item.content_items, audio_enabled=audio_enabled),
        }
    if not item.attachments:
        return {"role": "user", "content": item.content}
    content: list[dict[str, Any]] = [{"type": "text", "text": item.content}]
    content.extend(
        {
            "type": "image_url",
            "image_url": {"url": attachment.data_url, "detail": attachment.detail},
        }
        for attachment in item.attachments
    )
    return {"role": "user", "content": content}


def _attachment_messages(attachments: Iterable[Any]) -> list[dict[str, Any]]:
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Image returned by a tool."},
                {
                    "type": "image_url",
                    "image_url": {"url": attachment.data_url, "detail": attachment.detail},
                },
            ],
        }
        for attachment in attachments
    ]


def _text_from_delta(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and item.get("type") in {"text", "output_text"}
        )
    return ""


def _decode_stream_object(data: str, error: str) -> dict[str, Any]:
    """Decode one SSE data field without leaking provider shape errors."""

    try:
        value = json.loads(data)
    except json.JSONDecodeError as exc:
        raise ModelError(error, kind=ModelErrorKind.PROTOCOL, retryable=True) from exc
    if not isinstance(value, dict):
        raise _protocol_error(error)
    return value


def _protocol_error(message: str) -> ModelError:
    return ModelError(message, kind=ModelErrorKind.PROTOCOL)


def _finish_tool_call(buffer: _ToolCallBuffer) -> ToolCall:
    raw_arguments = buffer.arguments or "{}"
    parse_error: str | None = None
    arguments: dict[str, Any] | None
    try:
        parsed = json.loads(raw_arguments)
        if not isinstance(parsed, dict):
            raise ValueError("tool arguments must be a JSON object")
        arguments = parsed
    except (json.JSONDecodeError, ValueError) as exc:
        arguments = None
        parse_error = str(exc)
    return ToolCall(
        id=ToolCallId(buffer.id) if buffer.id else new_tool_call_id(),
        name=buffer.name,
        arguments=arguments,
        raw_arguments=raw_arguments,
        parse_error=parse_error,
    )


def _parse_usage(value: dict[str, Any]) -> ModelUsage:
    prompt_details = value.get("prompt_tokens_details") or {}
    completion_details = value.get("completion_tokens_details") or {}
    if not isinstance(prompt_details, dict) or not isinstance(completion_details, dict):
        raise _protocol_error("provider returned invalid token usage details")
    return ModelUsage(
        total_tokens=(
            _usage_integer(value["total_tokens"], "total tokens")
            if value.get("total_tokens") is not None
            else None
        ),
        input_tokens=_usage_integer(
            value.get("prompt_tokens") or value.get("input_tokens") or 0,
            "input tokens",
        ),
        output_tokens=_usage_integer(
            value.get("completion_tokens") or value.get("output_tokens") or 0,
            "output tokens",
        ),
        cached_tokens=_usage_integer(
            prompt_details.get("cached_tokens") or value.get("prompt_cache_hit_tokens") or 0,
            "cached tokens",
        ),
        reasoning_tokens=_usage_integer(
            completion_details.get("reasoning_tokens") or 0,
            "reasoning tokens",
        ),
    )


def _usage_integer(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise _protocol_error(f"provider returned invalid {label}")
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError) as exc:
        raise _protocol_error(f"provider returned invalid {label}") from exc
    if parsed < 0 or (isinstance(value, float) and not value.is_integer()):
        raise _protocol_error(f"provider returned invalid {label}")
    return parsed


def _check_response_limit(total: int, limit: int) -> None:
    if total > limit:
        raise ModelError(
            f"model response exceeded hard limit ({limit} characters)",
            kind=ModelErrorKind.PROTOCOL,
        )
