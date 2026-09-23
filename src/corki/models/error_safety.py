"""Provider error projection before retry events or durable failure storage."""

import re

from corki.models.base import ModelError

_BEARER_CREDENTIAL = re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/=-]+")


def display_model_error(message: str, api_key: str | None) -> str:
    """Keep diagnostics readable without echoing configured or Bearer credentials."""
    safe = "".join(char if char.isprintable() or char == "\n" else " " for char in message[:4000])
    if api_key:
        safe = safe.replace(api_key, "[redacted]")
    return _BEARER_CREDENTIAL.sub(r"\1[redacted]", safe).strip()


def sanitized_model_error(error: ModelError, api_key: str | None) -> ModelError:
    message = display_model_error(str(error), api_key)
    if message == str(error):
        return error
    return ModelError(
        message,
        kind=error.kind,
        retryable=error.retryable,
        status_code=error.status_code,
        retry_after_seconds=error.retry_after_seconds,
    )
