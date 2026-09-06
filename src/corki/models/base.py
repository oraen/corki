"""Model transport boundary used by the graph."""

from __future__ import annotations

from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Protocol

from corki.models.types import ModelEvent, ModelRequest


class ModelErrorKind(StrEnum):
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    CONTEXT_WINDOW = "context_window"
    SERVER = "server"
    TRANSPORT = "transport"
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
