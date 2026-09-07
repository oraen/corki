"""Model-visible hosted output copies; original event payloads remain immutable."""

import json
import math
from dataclasses import replace

from corki.protocol.audio import wav_duration_seconds
from corki.protocol.items import HostedToolItem
from corki.protocol.truncation import TruncationPolicy


def truncate_output_text(text: str, policy: TruncationPolicy) -> str:
    data = text.encode("utf-8")
    budget = policy.byte_budget
    if len(data) <= budget:
        return text
    left, right = budget // 2, budget - budget // 2
    prefix = data[:left].decode("utf-8", errors="ignore")
    suffix = data[-right:].decode("utf-8", errors="ignore") if right else ""
    removed = (
        (len(data) - budget + 3) // 4
        if policy.mode == "tokens"
        else len(text) - len(prefix) - len(suffix)
    )
    unit = "tokens" if policy.mode == "tokens" else "chars"
    return f"{prefix}…{removed} {unit} truncated…{suffix}"


def truncate_output_body(body, policy: TruncationPolicy):
    if isinstance(body, str):
        return truncate_output_text(body, policy)
    remaining = policy.limit
    output = []
    omitted_text = omitted_audio = 0
    for part in body:
        kind = part["type"]
        if kind == "input_text":
            text = part["text"]
            if not text:
                continue
            if not remaining:
                omitted_text += 1
                continue
            byte_count = len(text.encode("utf-8"))
            cost = (byte_count + 3) // 4 if policy.mode == "tokens" else byte_count
            if cost <= remaining:
                output.append(part)
                remaining -= cost
            else:
                output.append(
                    {**part, "text": truncate_output_text(text, replace(policy, limit=remaining))}
                )
                remaining = 0
        elif kind == "input_audio":
            url = part["audio_url"]
            duration = wav_duration_seconds(url)
            tokens = (
                math.ceil(duration * 10)
                if duration is not None
                else (len(url.encode("utf-8")) + 3) // 4
            )
            cost = tokens * 4 if policy.mode == "bytes" else tokens
            if cost <= remaining:
                output.append(part)
                remaining -= cost
            else:
                omitted_audio += 1
        else:
            output.append(part)
    if omitted_text:
        output.append({"type": "input_text", "text": f"[omitted {omitted_text} text items ...]"})
    if omitted_audio:
        output.append({"type": "input_text", "text": f"[omitted {omitted_audio} audio items ...]"})
    return output


def project_hosted_items(items, policy: TruncationPolicy):
    projected = []
    for item in items:
        if not isinstance(item, HostedToolItem) or item.model_payload_json is not None:
            projected.append(item)
            continue
        payload = json.loads(item.payload_json)
        if payload["type"] != "function_call_output":
            projected.append(item)
            continue
        effective = (
            TruncationPolicy("tokens", item.fallback_token_limit_override)
            if item.fallback_token_limit_override is not None
            else policy.history_allowance()
        )
        body = truncate_output_body(payload["output"], effective)
        if body == payload["output"]:
            projected.append(item)
            continue
        payload["output"] = body
        value = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        projected.append(replace(item, model_payload_json=value))
    return tuple(projected)
