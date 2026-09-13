"""Task-local request timeout without changing a shared MCP client's settings."""

from contextlib import contextmanager
from contextvars import ContextVar

_timeouts: ContextVar[tuple[tuple[object, float | None], ...]] = ContextVar(
    "mcp_request_timeouts", default=()
)


@contextmanager
def request_timeout(client: object, seconds: float | None):
    """Bind one exact client's policy for this invocation and its request tasks."""
    token = _timeouts.set((*_timeouts.get(), (client, seconds)))
    try:
        yield
    finally:
        _timeouts.reset(token)


def effective_timeout(client: object, default: float) -> float:
    """Unrelated clients do not inherit the invoking client's timeout policy."""
    for owner, seconds in reversed(_timeouts.get()):
        if owner is client:
            return default if seconds is None else seconds
    return default
