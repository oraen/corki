"""HTTP failures after the request retry layer, not Responses SSE errors."""

import json

import httpx

from corki.models.base import ModelError, ModelErrorKind


def http_error(response: httpx.Response, body: str) -> ModelError:
    status = response.status_code
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        parsed = None
    error = parsed.get("error") if isinstance(parsed, dict) else None
    error = error if isinstance(error, dict) else {}
    code = error.get("code")
    message = error.get("message")
    if not isinstance(message, str) or not message.strip():
        message = body
    retryable = False
    if status == 402:
        # Payment is an external prerequisite, not a transient stream failure.
        # Apply equally to every ordinary compatible provider and response body.
        kind = ModelErrorKind.PROTOCOL
    elif status == 503 and code in ("server_is_overloaded", "slow_down"):
        kind = ModelErrorKind.SERVER_OVERLOADED
    elif status in (400, 403) and code == "misalignment_policy_violation":
        kind = ModelErrorKind.MISALIGNMENT_POLICY
    elif status == 400:
        if code == "cyber_policy":
            kind = ModelErrorKind.CYBER_POLICY
        elif "The image data you provided does not represent a valid image" in body:
            kind = ModelErrorKind.INVALID_IMAGE
        else:
            kind = ModelErrorKind.INVALID_REQUEST
    elif status == 429:
        usage_type = error.get("type") if _valid_usage_error(error) else None
        kind = {
            "usage_limit_reached": ModelErrorKind.USAGE_LIMIT,
            "usage_not_included": ModelErrorKind.USAGE_NOT_INCLUDED,
        }.get(usage_type, ModelErrorKind.RETRY_LIMIT)
    else:
        # Ordinary provider credentials, permissions and endpoint configuration
        # cannot be repaired by repeating the same request. There is no
        # account-token refresh path for the model transport.
        retryable = status >= 500
        kind = (
            ModelErrorKind.AUTHENTICATION
            if status in (401, 403)
            else ModelErrorKind.SERVER
            if status >= 500
            else ModelErrorKind.PROTOCOL
        )
    return ModelError(
        f"model request failed ({status}): {message[:4000]}",
        kind=kind,
        retryable=retryable,
        status_code=status,
    )


def _valid_usage_error(error: dict) -> bool:
    for key in ("type", "plan_type"):
        value = error.get(key)
        if value is not None and not isinstance(value, str):
            return False
    resets = error.get("resets_at")
    return resets is None or (type(resets) is int and -(2**63) <= resets < 2**63)
