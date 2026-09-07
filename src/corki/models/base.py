"""Model transport boundary used by the graph."""

from __future__ import annotations

from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Protocol

from corki.models.types import ModelEvent, ModelRequest


class ModelErrorKind(StrEnum):
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    QUOTA = "quota"
    USAGE_NOT_INCLUDED = "usage_not_included"
    INVALID_REQUEST = "invalid_request"
    CYBER_POLICY = "cyber_policy"
    MISALIGNMENT_POLICY = "misalignment_policy"
    SERVER_OVERLOADED = "server_overloaded"
    USAGE_LIMIT = "usage_limit"
    RETRY_LIMIT = "retry_limit"
    INVALID_IMAGE = "invalid_image"
    CONTEXT_WINDOW = "context_window"
    OUTPUT_LIMIT = "output_limit"
    SERVER = "server"
    TRANSPORT = "transport"
    CONNECTION = "connection"
    PROTOCOL = "protocol"


class ModelError(RuntimeError):
    """A normalized provider failure carrying retry and status metadata."""

    def __init__(
        self,
        message: str,
        *,
        kind: ModelErrorKind = ModelErrorKind.PROTOCOL,
        retryable: bool = False,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


class ModelPort(Protocol):
    """Stream one model step as provider-independent events."""

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]: ...

    async def aclose(self) -> None: ...
