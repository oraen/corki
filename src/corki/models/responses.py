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

from corki import http_client
from corki.media.audio import UNSUPPORTED_INPUT
from corki.models.backoff import backoff, retry_limit
from corki.models.base import ModelError, ModelErrorKind
from corki.models.capabilities import ProviderCapabilities, StructuredOutputProtocol
from corki.models.error_safety import sanitized_model_error
from corki.models.freeform import compatible_arguments, compatible_call
from corki.models.http_stream import model_http_stream
from corki.models.item_metadata import annotate_response_input, capture_response_identity
from corki.models.media import response_content
from corki.models.namespaces import group_tool_definitions, request_tool_aliases, response_tool_name
from corki.models.openai_compatible import (
    _check_response_limit,
    _protocol_error,
)
from corki.models.request_metadata import filter_request_metadata
from corki.models.request_settings import resolve_reasoning, resolve_service_tier
from corki.models.response_completion import parse_response_completion
from corki.models.response_content import ResponseContent
from corki.models.response_errors import failed_response, incomplete_response
from corki.models.response_wire import decode_response_event
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
from corki.prompting.compaction import render_compaction_summary
from corki.protocol.context_messages import context_message_groups
from corki.protocol.hosted import is_hosted_tool_payload
from corki.protocol.ids import ToolCallId
from corki.protocol.items import (
    AssistantMessageItem,
    BudgetNoticeItem,
    CompactionItem,
    ContextItem,
    ConversationItem,
    HostedToolItem,
    ReasoningItem,
    RemoteHistoryItem,
    ToolCallItem,
    ToolResultItem,
    TurnAbortedItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.response_body import response_body_payload
from corki.protocol.response_items import (
    response_item_from_value,
)
from corki.protocol.tool_names import response_call_name
from corki.protocol.tools import ToolCall, ToolExposure
from corki.protocol.wire_numbers import dumps_wire


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
        self._client = (
            client if client is not None else http_client.OwnedHTTPClient(timeout=timeout_seconds)
        )
        self._owns_client = client is None

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Actual transport capabilities, including for host-supplied adapters."""
        return self._capabilities

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        if not self._base_url:
            raise ModelError(
                "No model base URL configured. Set provider.base_url or CORKI_API_BASE.",
                kind=ModelErrorKind.INVALID_REQUEST,
            )
        if request.compaction_turn_id is not None or request.compaction_mode != "v2":
            raise ModelError(
                "dedicated compaction is no longer supported; use Runtime.compact()",
                kind=ModelErrorKind.PROTOCOL,
            )
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
                        if isinstance(
                            event, (ModelTextDelta, ModelReasoningDelta, ModelItemCompleted)
                        ):
                            emitted_data = True
                        yield event
                return
            except ModelError as exc:
                safe_error = sanitized_model_error(exc, self._api_key)
                if (
                    request.harness_managed_retries
                    or emitted_data
                    or not exc.retryable
                    or attempt >= self._max_retries
                ):
                    if safe_error is exc:
                        raise
                    raise safe_error from None
                attempt += 1
                delay = exc.retry_after_seconds
                if delay is None:
                    delay = backoff(self._retry_base_seconds, attempt - 1)
                yield ModelRetrying(attempt, self._max_retries, delay, str(safe_error))
                await asyncio.sleep(delay)

    def _build_payload(self, request: ModelRequest) -> dict[str, Any]:
        request_tool_aliases(request)
        tools = group_tool_definitions(
            [
                tool
                for tool in request.tools
                if tool.exposure in {ToolExposure.DIRECT, ToolExposure.DIRECT_MODEL_ONLY}
            ],
        )
        payload: dict[str, Any] = {
            "model": request.model,
            "instructions": request.instructions,
            "input": [
                _to_response_message(
                    item,
                    audio_enabled=self._capabilities.supports_audio_input,
                )
                for item in context_message_groups((*request.context_items, *request.items))
            ],
            "stream": True,
        }
        if tools and self._capabilities.supports_tools:
            payload["tools"] = tools
            payload["parallel_tool_calls"] = self._capabilities.supports_parallel_tools
        reasoning = resolve_reasoning(request, self._reasoning_effort)
        tier = resolve_service_tier(request)
        if tier is not None and self._capabilities.supports_service_tier:
            payload["service_tier"] = tier
        if self._capabilities.supports_reasoning_effort and (
            request.model_info is not None
            or request.reasoning_summary is not None
            or reasoning.get("effort") is not None
        ):
            payload["reasoning"] = reasoning
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
        payload["input"] = filter_request_metadata(
            payload["input"],
        )
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

        def finish_call(buffer: _FunctionBuffer) -> ToolCall:
            return compatible_call(_finish_function_call(buffer), request)

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
                encode_json=dumps_wire,
            ) as response:
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        event = decode_response_event(data)
                    except ModelError:
                        # Codex's SSE parser skips undecodable envelopes. Do not
                        # log payload contents; a valid terminal is still required.
                        logging.getLogger(__name__).debug(
                            "Ignoring undecodable Responses SSE event"
                        )
                        continue
                    event_type = str(event.get("type", ""))
                    if event_type.startswith("response.custom_tool_call"):
                        raise _protocol_error("native custom tool protocol is not supported")
                    if event_type.startswith("response.tool_search_"):
                        raise _protocol_error("native tool search protocol is not supported")
                    if event_type.startswith(
                        ("response.compaction", "response.context_compaction")
                    ):
                        raise _protocol_error("dedicated compaction protocol is not supported")
                    if event_type.startswith(
                        ("response.web_search_call", "response.function_call_output")
                    ):
                        raise _protocol_error("hosted tool protocol is not supported")
                    if event_type in ("response.output_item.added", "response.output_item.done"):
                        _reject_native_tool_item(event.get("item"))
                        try:
                            # Validate before previews, hosted publication or live
                            # dispatch. Raw compatible/archive fields stay separate
                            # from the typed projection used for remote history.
                            response_item_from_value(event.get("item"))
                        except ValueError:
                            continue
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
                    elif event_type == "response.reasoning_summary_part.added":
                        # A section boundary is observable even before its first
                        # text arrives. Reuse the normalized, identity-bearing
                        # empty delta; no provider-specific runtime branch.
                        yield content.delta(
                            {**event, "type": "response.reasoning_summary_text.delta", "delta": ""},
                            reasoning=True,
                        )
                    elif event_type == "response.output_item.added":
                        item = event.get("item") or {}
                        if not isinstance(item, dict):
                            raise _protocol_error("Responses output item is not an object")
                        if item.get("type") in {"message", "reasoning"}:
                            content.added(item, event)
                        if item.get("type") == "function_call":
                            key = _function_item_key(item, event, calls)
                            buffer = calls.setdefault(key, _FunctionBuffer())
                            buffer.call_id = item["call_id"]
                            buffer.name = response_tool_name(item)
                            initial_arguments = item["arguments"]
                            if initial_arguments:
                                total_chars += max(
                                    0, len(initial_arguments) - len(buffer.arguments)
                                )
                                _check_response_limit(
                                    total_chars + content.extra_chars, self._response_char_limit
                                )
                                buffer.arguments = initial_arguments
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
                        if item.get("type") == "function_call":
                            key = _function_item_key(item, event, calls)
                            buffer = calls.setdefault(key, _FunctionBuffer())
                            buffer.call_id = item["call_id"]
                            buffer.name = response_tool_name(item)
                            arguments = item["arguments"]
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
                        if item.get("type") == "function_call":
                            call = finish_call(calls[key])
                            if key in finished_calls:
                                if (
                                    finished_calls[key].response_item_metadata_json
                                    != capture_response_identity(item)
                                    or finished_calls[key].call != call
                                ):
                                    raise _protocol_error("completed tool item changed")
                            else:
                                finished_calls[key] = ToolCallItem(
                                    call,
                                    turn_id,
                                    step_id,
                                    response_item_metadata_json=capture_response_identity(item),
                                )
                                finished_items.append(finished_calls[key])
                                total_chars += len(
                                    finished_calls[key].response_item_metadata_json or ""
                                )
                                _check_response_limit(
                                    total_chars + content.extra_chars, self._response_char_limit
                                )
                                yield ModelItemCompleted(finished_calls[key])
                    elif event_type == "response.completed":
                        raw_response = event.get("response")
                        if raw_response is None:
                            continue
                        try:
                            decoded = parse_response_completion(raw_response)
                        except ModelError as error:
                            response_error = error
                            continue
                        usage, end_turn = decoded.usage, decoded.end_turn
                        metadata.update(decoded.provider_metadata)
                        terminal = True
                        if raw_response.get("id"):
                            metadata["response_id"] = raw_response["id"]
                        if raw_response.get("model"):
                            metadata["model"] = raw_response["model"]
                        output = raw_response.get("output") or []
                        if not isinstance(output, list):
                            raise _protocol_error("Responses output is not a list")
                        for output_index, output_item in enumerate(output):
                            _reject_native_tool_item(output_item)
                            try:
                                # Compatible completed.output must not resurrect
                                # an item rejected by the streamed done boundary.
                                response_item_from_value(output_item)
                            except ValueError:
                                continue
                            if output_item.get("type") in {"message", "reasoning"}:
                                complete_item = content.complete(
                                    output_item, {"output_index": output_index}
                                )
                                _check_response_limit(
                                    total_chars + content.extra_chars, self._response_char_limit
                                )
                                if complete_item is not None:
                                    finished_items.append(complete_item)
                                    yield ModelItemCompleted(complete_item)
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
                                        output_item["call_id"],
                                        response_tool_name(output_item),
                                        output_item["arguments"],
                                    )
                                ):
                                    raise _protocol_error("completed response changed a tool item")
                                if key not in finished_calls:
                                    arguments = output_item["arguments"]
                                    previous = calls[key].arguments if key in calls else ""
                                    total_chars += max(0, len(arguments) - len(previous))
                                    _check_response_limit(
                                        total_chars + content.extra_chars, self._response_char_limit
                                    )
                                    calls[key] = _FunctionBuffer(
                                        call_id=output_item["call_id"],
                                        name=response_tool_name(output_item),
                                        arguments=arguments,
                                    )
                            if (
                                output_item.get("type") == "function_call"
                                and key in finished_calls
                                and (
                                    finished_calls[key].response_item_metadata_json
                                    != capture_response_identity(output_item)
                                )
                            ):
                                raise _protocol_error("completed response changed tool item kind")
                            if (
                                output_item.get("type") == "function_call"
                                and key not in finished_calls
                            ):
                                finished_calls[key] = ToolCallItem(
                                    finish_call(calls[key]),
                                    turn_id,
                                    step_id,
                                    response_item_metadata_json=capture_response_identity(
                                        output_item
                                    ),
                                )
                                finished_items.append(finished_calls[key])
                                total_chars += len(
                                    finished_calls[key].response_item_metadata_json or ""
                                )
                                _check_response_limit(
                                    total_chars + content.extra_chars, self._response_char_limit
                                )
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
        # Added/delta buffers are previews, not completed executable tool calls.
        yield ModelCompleted(
            tuple(finished_items), usage=usage, provider_metadata=metadata, end_turn=end_turn
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _to_response_message(item, **options) -> dict[str, Any]:
    if not isinstance(item, tuple):
        return _to_response_input(item, **options)
    first = _to_response_input(item[0], **options)
    if len(item) == 1:
        return first
    # Membership was frozen before append. Only serialization happens here;
    # never merge an adjacent later update into this message.
    first["content"] = [{"type": "input_text", "text": part.content} for part in item]
    return first


def _to_response_input(item: ConversationItem, **options) -> dict[str, Any]:
    payload = annotate_response_input(item, _response_input_content(item, **options))
    if (
        not options.get("audio_enabled", False)
        and payload.get("type", "message") == "message"
        and isinstance(payload.get("content"), list)
    ):
        payload = {
            **payload,
            "content": [
                {"type": "input_text", "text": UNSUPPORTED_INPUT}
                if part["type"] == "input_audio"
                else part
                for part in payload["content"]
            ],
        }
    return payload


def _response_input_content(
    item: ConversationItem,
    *,
    audio_enabled: bool = False,
) -> dict[str, Any]:
    if isinstance(item, HostedToolItem):
        # Preserve old facts as labelled data, never resurrect native wire items.
        return {"role": "user", "content": item.compatibility_content}
    if isinstance(item, UserMessageItem):
        if item.content_items:
            return {
                "role": "user",
                "content": response_content(item.content_items, audio_enabled=True),
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
        body = response_body_payload(item.response_body_json, "message")
        parts = (
            body["content"] if body is not None else [{"type": "output_text", "text": item.content}]
        )
        return {
            "role": "assistant",
            "content": parts,
            **({"phase": item.phase} if item.phase is not None else {}),
        }
    if isinstance(item, ReasoningItem):
        body = response_body_payload(item.response_body_json, "reasoning")
        if body is not None:
            return {
                "type": "reasoning",
                **body,
                "encrypted_content": item.encrypted_content,
                **({"id": item.provider_item_id} if item.provider_item_id else {}),
            }
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
            **response_call_name(item.call.name),
            "arguments": compatible_arguments(item.call),
        }
    if isinstance(item, ToolResultItem):
        output: object = item.content
        if item.content_items:
            output = response_content(item.content_items, audio_enabled=audio_enabled)
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
            "type": "function_call_output",
            "call_id": str(item.call_id),
            "output": output,
        }
    if isinstance(item, ContextItem):
        return {"role": item.role.value, "content": [{"type": "input_text", "text": item.content}]}
    if isinstance(item, TurnAbortedItem):
        return {"role": "user", "content": item.content}
    if isinstance(item, BudgetNoticeItem):
        return {"role": "developer", "content": item.content}
    if isinstance(item, RemoteHistoryItem):
        payload = item.payload
        if payload["type"] != "message":
            raise ModelError(
                "dedicated compaction history cannot be replayed through ordinary Responses; "
                "the original archive is preserved and requires migration",
                kind=ModelErrorKind.INVALID_REQUEST,
            )
        return payload
    if isinstance(item, CompactionItem):
        if item.remote_payload_json is not None:
            raise ModelError(
                "dedicated compaction history cannot be replayed through ordinary Responses; "
                "the original archive is preserved and requires migration",
                kind=ModelErrorKind.INVALID_REQUEST,
            )
        return {
            "role": "user",
            "content": [{"type": "input_text", "text": render_compaction_summary(item.summary)}],
        }
    raise TypeError(f"unsupported Responses input item: {type(item).__name__}")


def _function_item_key(item, event, calls) -> str:
    if item.get("id") is not None:
        return str(item["id"])
    if event.get("output_index") is not None:
        return str(event["output_index"])
    return next(
        (key for key, buffer in calls.items() if buffer.call_id == item["call_id"]),
        str(len(calls)),
    )


def _finish_function_call(buffer: _FunctionBuffer) -> ToolCall:
    raw = buffer.arguments
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("tool arguments must be a JSON object")
        # This cache must survive model commit. MCP parses the authoritative raw
        # text itself, including valid numbers outside Python's float range.
        json.dumps(parsed, ensure_ascii=False, allow_nan=False).encode("utf-8")
        arguments, parse_error = parsed, None
    except (ValueError, RecursionError) as exc:
        arguments, parse_error = None, str(exc)
    return ToolCall(
        ToolCallId(buffer.call_id),
        buffer.name,
        arguments,
        raw_arguments=raw,
        parse_error=parse_error,
    )


def _reject_native_tool_item(item) -> None:
    if isinstance(item, dict) and item.get("type") in {
        "compaction",
        "compaction_trigger",
        "context_compaction",
    }:
        raise _protocol_error("dedicated compaction output is not supported")
    if (
        isinstance(item, dict)
        and item.get("type") == "function_call"
        and item.get("namespace") not in (None, "")
    ):
        raise _protocol_error(
            "native namespace responses are unsupported; return the flat function name"
        )
    if isinstance(item, dict) and item.get("type") in {"tool_search_call", "tool_search_output"}:
        raise _protocol_error("native tool search protocol is not supported")
    if isinstance(item, dict) and is_hosted_tool_payload(item):
        raise _protocol_error("hosted tool protocol is not supported")
    if isinstance(item, dict) and item.get("type") in {
        "custom_tool_call",
        "custom_tool_call_output",
    }:
        raise _protocol_error("native custom tool protocol is not supported")
