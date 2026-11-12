"""Qoder model-server transport; no Qoder Agent, CLI subprocess or task execution."""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import time
import uuid
from urllib.parse import urlsplit

import aiohttp
import httpx

from corki_adaptor.common import MAX_BODY, CredentialsUnavailable, certificate_failure
from corki_adaptor.qoder.models import model_id
from corki_adaptor.streaming import NEED_INPUT, atransform, transform

UPSTREAM = "https://api2-v2.qoder.sh/model/v1/chat/completions"
MAX_STREAM_BYTES = 64 * 1024 * 1024
# Local preflight budget, not a discovered guarantee of upstream model capacity.
MAX_REQUEST_BYTES = 8 * 1024 * 1024


class ModelUnavailable(ValueError):
    """The model-server explicitly rejected this model identifier."""


def _event_parser():
    """SSE byte framing preserves Unicode line separators inside valid JSON strings."""
    buffer, data, total, finished = b"", [], 0, False
    while True:
        chunk = yield NEED_INPUT
        if chunk is None:
            break
        total += len(chunk)
        if total > MAX_STREAM_BYTES:
            raise ValueError("Qoder stream exceeds buffering limit")
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            line = line.removesuffix(b"\r")
            if line.startswith(b"data:"):
                data.append(line[5:].removeprefix(b" "))
            elif line.startswith(b":") and data and data[-1].rstrip().endswith(b'"'):
                # Observed fold between a JSON property name and its colon.
                # SSE would call this a comment; only join an unfinished JSON
                # event, never append to an already complete payload.
                try:
                    json.loads(b"\n".join(data))
                except (ValueError, UnicodeError):
                    data[-1] += line
            elif line and data and not line.startswith((b":", b"event:", b"id:", b"retry:")):
                # Model-server HTTP can fold one JSON data line into bare
                # continuation lines (observed even inside the completion ID).
                # Stay within this SSE event; never join across blank boundaries
                # or across a new data/control field, and still require valid JSON.
                try:
                    json.loads(b"\n".join(data))
                except (ValueError, UnicodeError):
                    data[-1] += line
                else:
                    raise ValueError("Unexpected SSE continuation")
            elif line == b"" and data:
                payload, data = b"\n".join(data), []
                if payload == b"[DONE]":
                    finished = True
                    break
                if payload.startswith(b"[NOTIFICATIONS]#"):
                    continue
                value = json.loads(payload)
                if value is None:  # Qoder model-server keepalive frame.
                    continue
                if isinstance(value, dict) and "statusCodeValue" in value and "body" in value:
                    if value["statusCodeValue"] != 200:
                        raise ValueError("Qoder stream returned an upstream error")
                    value = json.loads(value["body"])
                if isinstance(value, dict) and value.get("code") == "invalid_model_error":
                    raise ModelUnavailable("Qoder model-server does not support this model")
                if not isinstance(value, dict) or "error" in value or value.get("code"):
                    raise ValueError("Qoder stream returned an error")
                yield value
        if finished:
            break
    if not finished:
        raise ValueError("Qoder stream ended before [DONE]")


def public_chunk(value, model):
    choices = value.get("choices", [])
    if not isinstance(choices, list):
        raise ValueError("Invalid Qoder choices")
    return {
        "id": value.get("id", ""),
        "object": "chat.completion.chunk",
        "created": value.get("created", int(time.time())),
        "model": model,
        "choices": choices,
        **({"usage": value["usage"]} if value.get("usage") else {}),
    }


def _collect_parser(model):
    result = {
        "id": "",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [],
    }
    choices = {}
    while True:
        raw = yield NEED_INPUT
        if raw is None:
            break
        value = public_chunk(raw, model)
        result.update({k: value[k] for k in ("id", "created", "model")})
        if value.get("usage"):
            result["usage"] = value["usage"]
        for choice in value["choices"]:
            index = choice.get("index", 0)
            row = choices.setdefault(
                index,
                {
                    "index": index,
                    "message": {"role": "assistant", "content": ""},
                    "finish_reason": None,
                },
            )
            delta = choice.get("delta") or {}
            for key in ("content", "reasoning_content"):
                if delta.get(key) is not None:
                    row["message"][key] = row["message"].get(key, "") + delta[key]
            for call in delta.get("tool_calls") or []:
                calls = row["message"].setdefault("tool_calls", [])
                target = call.get("index", 0)
                if not isinstance(target, int) or not 0 <= target < 128:
                    raise ValueError("Invalid tool call index")
                while len(calls) <= target:
                    calls.append(
                        {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                    )
                current = calls[target]
                if call.get("id"):
                    current["id"] = call["id"]
                for key in ("name", "arguments"):
                    current["function"][key] += (call.get("function") or {}).get(key) or ""
            if choice.get("finish_reason"):
                row["finish_reason"] = choice["finish_reason"]
    if not choices or any(not c["finish_reason"] for c in choices.values()):
        raise ValueError("Qoder response did not finish")
    result["choices"] = [choices[i] for i in sorted(choices)]
    yield result


def events(chunks):
    yield from transform(_event_parser(), chunks)


def collect(chunks, model):
    return next(transform(_collect_parser(model), chunks))


async def collect_async(chunks, model):
    async for result in atransform(_collect_parser(model), chunks):
        return result
    raise ValueError("Missing Qoder completion")


async def forward(handler, endpoint):
    handler.close_connection = True
    if not hmac.compare_digest(
        handler.headers.get("Authorization", "").encode(), ("Bearer " + handler.server.key).encode()
    ):
        return handler.error(401, "Invalid local gateway key.")
    budget_only = endpoint in {"/request-budget", "/v1/request-budget"}
    if not budget_only and endpoint not in {"/chat/completions", "/v1/chat/completions"}:
        return handler.error(404, "Qoder 适配请使用 Chat Completions 协议。")
    sizes = handler.headers.getall("Content-Length", [])
    if handler.headers.get("Transfer-Encoding") or len(sizes) != 1:
        return handler.error(400, "A single Content-Length is required.")
    try:
        length = int(sizes[0])
        if not 0 < length <= MAX_BODY:
            return handler.error(413, "Request body exceeds gateway limits.")
        raw = await handler.read_body()
        if len(raw) != length:
            raise ValueError("Incomplete request")
        payload = json.loads(raw)
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("model"), str)
            or not payload["model"].strip()
        ):
            raise ValueError("A nonempty model name is required")
        if not isinstance(payload.get("stream", False), bool) or not isinstance(
            payload.get("messages"), list
        ):
            raise ValueError("Invalid messages or stream flag")
    except (ValueError, TypeError, UnicodeError):
        return handler.error(400, "Invalid Chat Completions request.")
    streaming = payload.get("stream", False)
    requested_model = payload["model"]
    payload["model"] = model_id(requested_model)
    # Chat Completions permits an empty system message, which Corki emits when
    # the model test has no instructions. Qoder's Kimi route rejects that row
    # with provider_error; omitting it preserves the request's instructions.
    payload["messages"] = [
        message
        for message in payload["messages"]
        if not (
            isinstance(message, dict)
            and message.get("role") == "system"
            and message.get("content") == ""
        )
    ]
    request, session = str(uuid.uuid4()), str(uuid.uuid4())
    payload.update(
        stream=True,
        stream_options={"include_usage": True},
        metadata={
            "context": {
                "request_id": request,
                "request_set_id": request,
                "session_id": session,
                "task_id": session,
                "client_type": "5",
            }
        },
    )
    if budget_only:
        # Match the upstream transport's actual JSON encoding after all gateway transformations.
        # Never contact Qoder or read its credentials for a budget-only request.
        def size(value):
            encoded = json.dumps(
                value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
            return len(encoded)

        fixed = {
            **payload,
            "messages": [
                m
                for m in payload["messages"]
                if isinstance(m, dict) and m.get("role") in ("system", "developer")
            ],
        }
        try:
            budget = {
                "protocol_version": 1,
                "request_bytes": size(payload),
                "fixed_bytes": size(fixed),
                "limit_bytes": MAX_REQUEST_BYTES,
            }
        except (ValueError, TypeError, UnicodeError):
            return handler.error(400, "Invalid JSON request.")
        return handler.json_response(200, budget)
    started = False
    try:
        token = (await asyncio.to_thread(handler.server.qoder_credentials)).token
        upstream = os.environ.get("QODER_UPSTREAM", UPSTREAM)
        parsed = urlsplit(upstream)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Qoder upstream must use HTTPS")
        async with handler.server.client.stream(
            "POST",
            upstream,
            json=payload,
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "text/event-stream",
                "X-Request-ID": request,
                "X-Session-ID": session,
            },
        ) as response:
            if response.status_code != 200:
                return handler.error(
                    response.status_code if 400 <= response.status_code < 600 else 502,
                    f"Qoder upstream returned HTTP {response.status_code}.",
                )
            if not response.headers.get("content-type", "").startswith("text/event-stream"):
                raise ValueError("Expected event stream")
            chunks = atransform(_event_parser(), response.aiter_bytes())
            # Validate the first frame before committing streaming HTTP headers.
            first = await anext(chunks)

            async def with_first():
                yield first
                async for value in chunks:
                    yield value

            values = with_first()
            if not streaming:
                return handler.json_response(200, await collect_async(values, requested_model))
            await handler.start_stream("text/event-stream; charset=utf-8")
            started = True
            terminal = False
            async for value in values:
                value = public_chunk(value, requested_model)
                terminal |= any(c.get("finish_reason") for c in value["choices"])
                await handler.write(
                    b"data: " + json.dumps(value, ensure_ascii=False).encode() + b"\n\n"
                )
            if not terminal:
                raise ValueError("No terminal choice")
            await handler.write(b"data: [DONE]\n\n")
    except ModelUnavailable:
        if started:
            handler.abort()
        if not started:
            handler.error(400, "Qoder 当前模型接口不支持此模型，请选择已提供的模型。")
    except CredentialsUnavailable:
        if started:
            handler.abort()
        if not started:
            handler.error(401, "Qoder 登录信息不可用，请检查登录状态和系统钥匙串授权。")
    except (
        httpx.ConnectError,
        httpx.ConnectTimeout,
        aiohttp.ClientConnectorError,
        aiohttp.ConnectionTimeoutError,
    ) as error:
        if started:
            handler.abort()
        if not started:
            if certificate_failure(error):
                return handler.error(
                    424,
                    "Qoder upstream certificate verification failed; check the system trust store.",
                )
            handler.error(
                502,
                "Cannot reach Qoder upstream; check network connectivity.",
                code="upstream_connection_failed",
            )
    except (
        httpx.HTTPError,
        aiohttp.ClientError,
        TimeoutError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        StopAsyncIteration,
    ):
        if not started:
            handler.error(502, "Qoder 模型请求失败，或上游响应不完整。")
        else:
            handler.abort()
        # After headers, close; never invent a successful terminal chunk.
