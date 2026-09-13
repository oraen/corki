"""Bounded native hosted-tool facts, without a local execution identity."""

from corki.protocol.wire_json import loads_wire, materialize

# Transport/storage protection, not the model's output truncation policy.
HOSTED_ITEM_MAX_BYTES = 32_000_000


def is_hosted_tool_payload(payload: dict) -> bool:
    kind = payload.get("type")
    return (
        kind in {"web_search_call", "tool_search_output"}
        or (kind == "tool_search_call" and payload.get("execution") == "server")
        or (kind == "function_call_output" and payload.get("call_id") is None)
    )


def decode_hosted_payload(value: str) -> dict:
    if not isinstance(value, str) or len(value.encode("utf-8")) > HOSTED_ITEM_MAX_BYTES:
        raise ValueError("hosted tool payload exceeds transport byte limit or is not text")
    payload = materialize(loads_wire(value), preserve_pairs=False)
    if not isinstance(payload, dict) or not is_hosted_tool_payload(payload):
        raise ValueError("unsupported hosted tool payload")
    for key in ("id", "call_id", "name", "namespace", "status"):
        if payload.get(key) is not None and not isinstance(payload[key], str):
            raise ValueError(f"hosted tool {key} must be text or null")
    kind = payload["type"]
    if kind == "function_call_output" and not isinstance(payload.get("output"), (str, list)):
        raise ValueError("hosted notification output must be text or an array")
    if kind == "function_call_output" and isinstance(payload["output"], list):
        fields = {
            "input_text": "text",
            "input_image": "image_url",
            "input_audio": "audio_url",
            "encrypted_content": "encrypted_content",
        }
        for part in payload["output"]:
            if not isinstance(part, dict) or part.get("type") not in fields:
                raise ValueError("unsupported hosted output content item")
            if not isinstance(part.get(fields[part["type"]]), str):
                raise ValueError("hosted output content value must be text")
            if part["type"] == "input_image" and part.get("detail") not in {
                None,
                "auto",
                "low",
                "high",
                "original",
            }:
                raise ValueError("invalid hosted image detail")
    if kind == "tool_search_output" and (
        not isinstance(payload.get("tools"), list)
        or not isinstance(payload.get("execution"), str)
        or not isinstance(payload.get("status"), str)
    ):
        raise ValueError("hosted search output requires tools, execution and status")
    if kind == "tool_search_call" and "arguments" not in payload:
        raise ValueError("hosted search call requires arguments")
    if (
        kind == "web_search_call"
        and payload.get("action") is not None
        and not isinstance(payload["action"], dict)
    ):
        raise ValueError("web search action must be an object or null")
    return payload
