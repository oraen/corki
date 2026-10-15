"""Small, endpoint/model-scoped envelope for Chat Completions replay data."""

from __future__ import annotations

import hashlib
import json
import re

SIGNATURE_LIMIT = 262_144


def replay_scope(base_url: str, model: str) -> str:
    # Persist no endpoint credentials or API keys in this envelope.
    return hashlib.sha256(json.dumps([base_url.rstrip("/"), model]).encode()).hexdigest()


def replay_payload(value: str | None) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, str) or len(value) > SIGNATURE_LIMIT * 6 + 256:
        raise ValueError("invalid chat replay envelope")
    payload = json.loads(value)
    if (
        not isinstance(payload, dict)
        or set(payload) - {"scope", "thought_signature"}
        or not isinstance(payload.get("scope"), str)
        or re.fullmatch(r"[0-9a-f]{64}", payload["scope"]) is None
    ):
        raise ValueError("invalid chat replay scope")
    if "thought_signature" in payload:
        signature = payload["thought_signature"]
        if not isinstance(signature, str) or not signature or len(signature) > SIGNATURE_LIMIT:
            raise ValueError("invalid chat thought signature")
    return payload


def encode_replay(base_url: str, model: str, signature: str | None = None) -> str:
    payload = {"scope": replay_scope(base_url, model)}
    if signature is not None:
        payload["thought_signature"] = signature
    value = json.dumps(payload)
    replay_payload(value)
    return value


def matched_replay(value: str | None, base_url: str, model: str) -> dict[str, str]:
    payload = replay_payload(value)
    return payload if payload.get("scope") == replay_scope(base_url, model) else {}
