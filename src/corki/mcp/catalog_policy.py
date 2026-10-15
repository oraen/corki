"""Host-selected catalog capacity scoped to one exact startup client."""

from contextlib import contextmanager
from contextvars import ContextVar

REGULAR_CATALOG_LIMIT = 2_048
_limits: ContextVar[tuple[tuple[object, int], ...]] = ContextVar("mcp_catalog_limits", default=())


def validate_catalog_limit(limit: int) -> None:
    if type(limit) is not int or limit != REGULAR_CATALOG_LIMIT:
        raise ValueError("MCP catalog capacity must be a host-selected standard limit")


@contextmanager
def catalog_limit(client: object, limit: int):
    validate_catalog_limit(limit)
    token = _limits.set((*_limits.get(), (client, limit)))
    try:
        yield
    finally:
        _limits.reset(token)


def effective_catalog_limit(client: object) -> int:
    for owner, limit in reversed(_limits.get()):
        if owner is client:
            return limit
    return REGULAR_CATALOG_LIMIT
