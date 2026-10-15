"""Lifecycle matchers: wildcard, exact alternatives, then native regex search."""

from corki.config.mcp_regex import _call


def _exact(pattern):
    return all(char.isascii() and (char.isalnum() or char in "_|") for char in pattern)


def validate(pattern):
    if pattern is None:
        return
    if not isinstance(pattern, str):
        raise ValueError("Hook matcher must be a string")
    if pattern in ("", "*") or _exact(pattern):
        return
    if _call("hook_validate", pattern):
        raise ValueError("Invalid hook matcher regex")


def matches(pattern, candidate):
    if pattern is None or pattern in ("", "*"):
        return True
    if _exact(pattern):
        return candidate in pattern.split("|")
    try:
        return _call("hook_matches", pattern, candidate) == 1
    except ValueError:
        return False
