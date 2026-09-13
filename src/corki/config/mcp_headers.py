"""Immutable optional HTTP MCP header configuration."""

from collections.abc import Mapping

RUST_WHITESPACE = (
    "\t\n\v\f\r \u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006"
    "\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
)


def header_pairs(value: object, field: str) -> tuple[tuple[str, str], ...] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        pairs = tuple(value.items())
    elif isinstance(value, (list, tuple)):
        pairs = tuple(value)
    else:
        raise ValueError(f"MCP {field} must be a string table")
    if not all(
        isinstance(pair, (list, tuple))
        and len(pair) == 2
        and all(isinstance(item, str) for item in pair)
        for pair in pairs
    ):
        raise ValueError(f"MCP {field} must be a string table")
    return tuple(dict(pairs).items())
