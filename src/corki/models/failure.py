"""Durable failed-attempt facts, distinct from a successful ModelCompleted."""

from dataclasses import dataclass

from corki.models.base import ModelError, ModelErrorKind


@dataclass(frozen=True)
class ModelFailure:
    message: str
    kind: str
    retryable: bool
    retries_used: int
    status_code: int | None = None
    retry_after_seconds: float | None = None
    connection_retries_used: int = 0

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
        )

    def error(self) -> ModelError:
        return ModelError(
            self.message,
            kind=ModelErrorKind(self.kind),
            retryable=self.retryable,
            status_code=self.status_code,
            retry_after_seconds=self.retry_after_seconds,
        )
