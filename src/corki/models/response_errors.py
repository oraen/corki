"""Responses SSE error codes, distinct from HTTP request failures."""

import math
import re
from contextlib import suppress

from corki.models.base import ModelError, ModelErrorKind
from corki.models.response_completion import _usage
from corki.models.retry_after import retry_after_header

_RATE_DELAY = re.compile(r"try again in\s*(\d+(?:\.\d+)?)\s*(s|ms|seconds?)", re.IGNORECASE)
_FATAL_CODES = {
    "context_length_exceeded": ModelErrorKind.CONTEXT_WINDOW,
    "insufficient_quota": ModelErrorKind.QUOTA,
    "usage_not_included": ModelErrorKind.USAGE_NOT_INCLUDED,
    "cyber_policy": ModelErrorKind.CYBER_POLICY,
    "misalignment_policy_violation": ModelErrorKind.MISALIGNMENT_POLICY,
    "invalid_prompt": ModelErrorKind.INVALID_REQUEST,
    "bio_policy": ModelErrorKind.INVALID_REQUEST,
    "server_is_overloaded": ModelErrorKind.SERVER_OVERLOADED,
    "slow_down": ModelErrorKind.SERVER_OVERLOADED,
}
_FALLBACKS = {
    "cyber_policy": "This request has been flagged for possible cybersecurity risk.",
    "misalignment_policy_violation": (
        "This request was blocked due to a misalignment policy violation."
    ),
}


def failed_response(response: object) -> ModelError:
    error = response.get("error") if isinstance(response, dict) else None
    if not _valid_error(error):
        return ModelError(
            "response.failed event received", kind=ModelErrorKind.SERVER, retryable=True
        )
    code, message = error.get("code"), error.get("message")
    headers = error.get("headers")
    retry_deadline = retry_after_header(headers) if isinstance(headers, dict) else None
    if code in _FALLBACKS and (message is None or not message.strip()):
        message = _FALLBACKS[code]
    elif code in {"invalid_prompt", "bio_policy"} and message is None:
        message = "Invalid request."
    kind = _FATAL_CODES.get(code)
    if kind is not None:
        return ModelError(
            message or code,
            kind=kind,
            retryable=kind is ModelErrorKind.SERVER_OVERLOADED and retry_deadline is not None,
            retry_after_deadline=retry_deadline,
        )
    return ModelError(
        message or "Responses request failed",
        kind=ModelErrorKind.RATE_LIMIT if code == "rate_limit_exceeded" else ModelErrorKind.SERVER,
        retryable=True,
        retry_after_seconds=_rate_delay(message)
        if code == "rate_limit_exceeded" and retry_deadline is None
        else None,
        retry_after_deadline=retry_deadline,
    )


def incomplete_response(response: object) -> ModelError:
    details = response.get("incomplete_details") if isinstance(response, dict) else None
    reason = details.get("reason") if isinstance(details, dict) else None
    if not isinstance(reason, str):
        reason = "unknown"
    usage = None
    if isinstance(response, dict) and response.get("usage") is not None:
        with suppress(ValueError, TypeError):
            usage = _usage(response["usage"])
    response_id = response.get("id") if isinstance(response, dict) else None
    return ModelError(
        f"Incomplete response returned, reason: {reason}",
        kind=ModelErrorKind.OUTPUT_LIMIT
        if reason == "max_output_tokens"
        else ModelErrorKind.RESPONSE_INCOMPLETE,
        retryable=False,
        usage=usage,
        response_id=response_id if isinstance(response_id, str) else None,
    )


def _valid_error(error: object) -> bool:
    if not isinstance(error, dict):
        return False
    if any(
        value is not None and not isinstance(value, str)
        for value in (error.get(key) for key in ("type", "code", "message", "plan_type"))
    ):
        return False
    resets = error.get("resets_at")
    return resets is None or (type(resets) is int and -(2**63) <= resets < 2**63)


def _rate_delay(message: str | None) -> float | None:
    match = _RATE_DELAY.search(message or "")
    if match is None:
        return None
    value = float(match[1])
    if not math.isfinite(value):
        return None
    # Codex uses Duration::from_millis(value as u64): discard fractional ms.
    return min(int(value), 2**64 - 1) / 1000 if match[2].lower() == "ms" else value
