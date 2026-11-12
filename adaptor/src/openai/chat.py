"""Chat Completions translation for the explicitly selected local Astra gateway."""

import json

from corki_adaptor.streaming import NEED_INPUT, atransform, transform


def to_responses(body: dict) -> dict:
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a nonempty array")
    if body.get("n", 1) != 1:
        raise ValueError("Only n=1 is supported")
    inputs, instructions = [], []
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError("Invalid message")
        role = message.get("role")
        content = message.get("content")
        if role == "system" and not inputs:
            # Corki groups developer ContextItems into one system message with
            # text content parts. Preserve their order just as string messages.
            if isinstance(content, list):
                if not all(
                    isinstance(part, dict)
                    and part.get("type") == "text"
                    and isinstance(part.get("text"), str)
                    for part in content
                ):
                    raise ValueError("System instructions must contain only text parts")
                content = "\n\n".join(part["text"] for part in content)
            if not isinstance(content, str):
                raise ValueError("System instructions must be text")
            instructions.append(content)
            continue
        if role == "tool":
            if not isinstance(message.get("tool_call_id"), str):
                raise ValueError("Tool output requires tool_call_id")
            inputs.append(
                {
                    "type": "function_call_output",
                    "call_id": message["tool_call_id"],
                    "output": content if isinstance(content, str) else json.dumps(content),
                }
            )
            continue
        if role not in {"system", "developer", "user", "assistant"}:
            raise ValueError("Unsupported message role")
        parts = []
        if isinstance(content, str) and content:
            parts.append(
                {"type": "output_text" if role == "assistant" else "input_text", "text": content}
            )
        elif isinstance(content, list):
            for part in content:
                if part.get("type") == "text":
                    parts.append(
                        {
                            "type": "output_text" if role == "assistant" else "input_text",
                            "text": part["text"],
                        }
                    )
                elif part.get("type") == "image_url" and role != "assistant":
                    image = part["image_url"]
                    parts.append(
                        {
                            "type": "input_image",
                            "image_url": image["url"],
                            "detail": image.get("detail", "auto"),
                        }
                    )
                else:
                    raise ValueError("Unsupported message content")
        elif content is not None and content != "":
            raise ValueError("Unsupported message content")
        if parts:
            inputs.append({"role": "developer" if role == "system" else role, "content": parts})
        for call in message.get("tool_calls", []):
            if role != "assistant" or call.get("type") != "function":
                raise ValueError("Only assistant function calls are supported")
            function = call["function"]
            inputs.append(
                {
                    "type": "function_call",
                    "call_id": call["id"],
                    "name": function["name"],
                    "arguments": function["arguments"],
                }
            )
    payload = {
        "model": body.get("model"),
        "instructions": "\n\n".join(instructions),
        "input": inputs,
        "stream": True,
        "store": False,
    }
    effort = body.get("reasoning_effort")
    if effort is not None:
        if not isinstance(effort, str) or not effort.strip():
            raise ValueError("reasoning_effort must be a nonempty string")
        payload["reasoning"] = {"effort": effort}
    if body.get("tools"):
        tools = []
        for tool in body["tools"]:
            if tool.get("type") != "function":
                raise ValueError("Only function tools are supported")
            tools.append({"type": "function", **tool["function"], "strict": False})
        payload["tools"] = tools
        payload["parallel_tool_calls"] = body.get("parallel_tool_calls", True)
    choice = body.get("tool_choice")
    if choice is not None:
        payload["tool_choice"] = (
            {"type": "function", "name": choice["function"]["name"]}
            if isinstance(choice, dict)
            else choice
        )
    fmt = body.get("response_format")
    if fmt:
        if fmt.get("type") == "json_schema":
            payload["text"] = {"format": {"type": "json_schema", **fmt["json_schema"]}}
        elif fmt.get("type") in {"json_object", "text"}:
            payload["text"] = {"format": fmt}
        else:
            raise ValueError("Unsupported response_format")
    return payload


def _chat_parser(model: str = "unknown"):
    """Translate SSE, emitting a successful finish only after response.completed."""
    emitted_text = False
    response_id = "chatcmpl-codex-gateway"

    def packet(delta, finish=None, usage=None):
        result = {
            "id": response_id,
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        if usage is not None:
            result["usage"] = usage
        return ("data: " + json.dumps(result, ensure_ascii=False) + "\n\n").encode()

    while True:
        line = yield NEED_INPUT
        if line is None:
            break
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if not raw or raw == "[DONE]":
            continue
        event = json.loads(raw)
        kind = event.get("type")
        response = event.get("response", {})
        response_id = response.get("id", response_id)
        model = response.get("model", model)
        if kind == "response.output_text.delta":
            emitted_text = True
            yield packet({"content": event["delta"]})
        elif kind in {"response.reasoning_summary_text.delta", "response.reasoning_text.delta"}:
            yield packet({"reasoning_content": event["delta"]})
        elif kind in {"response.failed", "response.incomplete", "error"}:
            raise ValueError("Codex returned an unsuccessful response")
        elif kind == "response.completed":
            if response.get("status", "completed") != "completed":
                raise ValueError("Codex response did not complete")
            calls = []
            for item in response.get("output", []):
                if item.get("type") == "function_call":
                    calls.append(
                        {
                            "index": len(calls),
                            "id": item["call_id"],
                            "type": "function",
                            "function": {"name": item["name"], "arguments": item["arguments"]},
                        }
                    )
                elif item.get("type") == "message" and not emitted_text:
                    for part in item.get("content", []):
                        if part.get("type") == "output_text":
                            yield packet({"content": part["text"]})
                elif item.get("type") not in {"message", "reasoning", "function_call"}:
                    raise ValueError("Unsupported Codex output item")
            if calls:
                yield packet({"tool_calls": calls})
            usage = response.get("usage", {})
            mapped = {
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
                "prompt_tokens_details": usage.get("input_tokens_details", {}),
                "completion_tokens_details": usage.get("output_tokens_details", {}),
            }
            yield packet({}, "tool_calls" if calls else "stop", mapped)
            yield b"data: [DONE]\n\n"
            return
    raise ValueError("Codex stream ended before response.completed")


def _completed_parser(max_bytes):
    """Wait for the authoritative completed response without retaining deltas."""
    size = 0
    while True:
        line = yield NEED_INPUT
        if line is None:
            break
        size += len(line.encode("utf-8"))
        if size > max_bytes:
            raise ValueError("Codex response exceeds the buffering limit")
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if not raw or raw == "[DONE]":
            continue
        event = json.loads(raw)
        if not isinstance(event, dict):
            raise ValueError("Invalid Codex event")
        kind = event.get("type")
        if kind in {"response.failed", "response.incomplete", "error"}:
            raise ValueError("Codex returned an unsuccessful response")
        if kind == "response.completed":
            response = event.get("response")
            if not isinstance(response, dict) or response.get("status", "completed") != "completed":
                raise ValueError("Codex response did not complete")
            yield response
            return
    raise ValueError("Codex stream ended before response.completed")


def chat_events(lines, model="unknown"):
    yield from transform(_chat_parser(model), lines)


def async_chat_events(lines, model="unknown"):
    return atransform(_chat_parser(model), lines)


def completed_response(lines, *, max_bytes=32 * 1024 * 1024):
    return next(transform(_completed_parser(max_bytes), lines))


async def async_completed_response(lines, *, max_bytes=32 * 1024 * 1024):
    async for response in atransform(_completed_parser(max_bytes), lines):
        return response
    raise ValueError("Missing completed response")


def chat_completion(response: dict, model: str) -> dict:
    """Reuse streaming field mapping with the final, complete response as input."""
    import time

    message = {"role": "assistant", "content": None}
    result = {
        "id": response.get("id", "chatcmpl-codex-gateway"),
        "object": "chat.completion",
        "created": response.get("created_at", int(time.time())),
        "model": response.get("model", model),
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
    }
    event = json.dumps({"type": "response.completed", "response": response})
    for chunk in chat_events(["data: " + event], model):
        if chunk == b"data: [DONE]\n\n":
            continue
        packet = json.loads(chunk.decode()[6:])
        choice = packet["choices"][0]
        delta = choice["delta"]
        if "content" in delta:
            message["content"] = (message["content"] or "") + delta["content"]
        if "tool_calls" in delta:
            message["tool_calls"] = [
                {k: v for k, v in call.items() if k != "index"} for call in delta["tool_calls"]
            ]
        if choice.get("finish_reason"):
            result["choices"][0]["finish_reason"] = choice["finish_reason"]
        if "usage" in packet:
            result["usage"] = packet["usage"]
    refusals = [
        part["refusal"]
        for item in response.get("output", [])
        if item.get("type") == "message"
        for part in item.get("content", [])
        if part.get("type") == "refusal"
    ]
    if refusals:
        message["refusal"] = "".join(refusals)
    return result
