"""Canonical tool identity and bounded, deterministic compatibility wire aliases."""

import hashlib


def split_tool_name(name: str) -> tuple[str | None, str]:
    namespace, separator, leaf = name.partition("::")
    return (namespace, leaf) if separator else (None, name)


def compatible_tool_name(name: str) -> str:
    if "::" not in name:
        return name
    return "corki_ns_" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:48]


def response_call_name(name: str, *, native_namespaces: bool) -> dict[str, str]:
    namespace, leaf = split_tool_name(name)
    if native_namespaces and namespace is not None:
        return {"name": leaf, "namespace": namespace}
    return {"name": compatible_tool_name(name)}
