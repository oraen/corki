"""Durable failed-attempt facts, distinct from a successful ModelCompleted."""

import time
from dataclasses import asdict, dataclass

from corki.models.base import ModelError, ModelErrorKind
from corki.models.types import ModelUsage


@dataclass(frozen=True)
class ModelFailure:
    message: str
    kind: str
    retryable: bool
    retries_used: int
    status_code: int | None = None
    retry_after_seconds: float | None = None
    connection_retries_used: int = 0
    usage: dict | None = None
    response_id: str | None = None
    retry_after_epoch: float | None = None

    @classmethod
    def from_error(cls, error: ModelError, retries_used: int, connection_retries_used: int = 0):
        return cls(
            str(error)[:4000],
            error.kind.value,
            error.retryable,
            retries_used,
            error.status_code,
            error.retry_after_seconds,
            connection_retries_used,
            asdict(error.usage) if error.usage is not None else None,
            error.response_id,
            time.time() + error.retry_after_seconds
            if error.retry_after_deadline is not None
            else None,
        )

    def error(self) -> ModelError:
        return ModelError(
            self.message,
            kind=ModelErrorKind(self.kind),
            # Keep immutable archived facts intact, but do not resurrect the
            # old policy that automatically retried payment-required failures.
            retryable=self.retryable and self.status_code != 402,
            status_code=self.status_code,
            retry_after_seconds=max(0.0, self.retry_after_epoch - time.time())
            if self.retry_after_epoch is not None
            else self.retry_after_seconds,
            usage=ModelUsage(**self.usage) if self.usage is not None else None,
            response_id=self.response_id,
        )
