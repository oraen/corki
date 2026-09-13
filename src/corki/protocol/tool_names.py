"""Canonical tool identity and bounded, deterministic compatibility wire aliases."""

import hashlib

# Rust str::trim follows Unicode White_Space, not Python's extra U+001C..001F.
NAMESPACE_WHITESPACE = (
    "\t\n\v\f\r \u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006"
    "\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
)


def has_namespace_description(description: str | None) -> bool:
    return bool(description and description.strip(NAMESPACE_WHITESPACE))


def split_tool_name(name: str) -> tuple[str | None, str]:
    namespace, separator, leaf = name.partition("::")
    return (namespace, leaf) if separator else (None, name)


def compatible_tool_name(name: str) -> str:
    if "::" not in name:
        return name
    return "corki_ns_" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:48]


def response_call_name(name: str, *, native_namespaces: bool) -> dict[str, str]:
    return {"name": compatible_tool_name(name)}
