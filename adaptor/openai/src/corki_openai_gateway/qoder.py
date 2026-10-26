"""Qoder model-server transport; no Qoder Agent, CLI subprocess or task execution."""

from __future__ import annotations

import hmac
import json
import os
import time
import uuid
from itertools import chain
from urllib.parse import urlsplit

import httpx

from corki_openai_gateway.gateway import MAX_BODY, CredentialsUnavailable
from corki_openai_gateway.qoder_models import model_id

UPSTREAM = "https://api2-v2.qoder.sh/model/v1/chat/completions"
MAX_STREAM_BYTES = 64 * 1024 * 1024


class ModelUnavailable(ValueError):
    """The model-server explicitly rejected this model identifier."""


def events(chunks):
    """SSE byte framing preserves Unicode line separators inside valid JSON strings."""
    buffer, data, total, finished = b"", [], 0, False
    for chunk in chunks:
        total += len(chunk)
        if total > MAX_STREAM_BYTES:
            raise ValueError("Qoder stream exceeds buffering limit")
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            line = line.removesuffix(b"\r")
            if line.startswith(b"data:"):
                data.append(line[5:].removeprefix(b" "))
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


def collect(chunks, model):
    result = {
        "id": "",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [],
    }
    choices = {}
    for raw in chunks:
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
    return result


def forward(handler, endpoint):
    handler.close_connection = True
    if not hmac.compare_digest(
        handler.headers.get("Authorization", "").encode(), ("Bearer " + handler.server.key).encode()
    ):
        return handler.error(401, "Invalid local gateway key.")
    if endpoint not in {"/chat/completions", "/v1/chat/completions"}:
        return handler.error(404, "Qoder 适配请使用 Chat Completions 协议。")
    sizes = handler.headers.get_all("Content-Length", [])
    if handler.headers.get("Transfer-Encoding") or len(sizes) != 1:
        return handler.error(400, "A single Content-Length is required.")
    try:
        length = int(sizes[0])
        if not 0 < length <= MAX_BODY:
            return handler.error(413, "Request body exceeds gateway limits.")
        raw = handler.rfile.read(length)
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
    started = False
    try:
        token = handler.server.qoder_credentials().token
        upstream = os.environ.get("QODER_UPSTREAM", UPSTREAM)
        parsed = urlsplit(upstream)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Qoder upstream must use HTTPS")
        with handler.server.client.stream(
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
            chunks = events(response.iter_bytes())
            # Validate the first frame before committing streaming HTTP headers.
            chunks = chain([next(chunks)], chunks)
            if not streaming:
                return handler.json_response(200, collect(chunks, requested_model))
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
            handler.send_header("Cache-Control", "no-cache")
            handler.send_header("Connection", "close")
            handler.end_headers()
            started = True
            terminal = False
            for value in chunks:
                value = public_chunk(value, requested_model)
                terminal |= any(c.get("finish_reason") for c in value["choices"])
                handler.wfile.write(
                    b"data: " + json.dumps(value, ensure_ascii=False).encode() + b"\n\n"
                )
                handler.wfile.flush()
            if not terminal:
                raise ValueError("No terminal choice")
            handler.wfile.write(b"data: [DONE]\n\n")
            handler.wfile.flush()
    except ModelUnavailable:
        if not started:
            handler.error(400, "Qoder 当前模型接口不支持此模型，请选择已提供的模型。")
    except CredentialsUnavailable:
        if not started:
            handler.error(401, "Qoder 登录信息不可用，请检查登录状态和系统钥匙串授权。")
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError, StopIteration):
        if not started:
            handler.error(502, "Qoder 模型请求失败，或上游响应不完整。")
        # After headers, close; never invent a successful terminal chunk.
