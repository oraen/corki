"""Step-bound deferred namespace state, independent of the BM25 source directory.

The complete map is durable comparison data. Only its escaped, bounded rendering
is model-visible; omitted entries must still participate in later diffs.
"""

import json
from dataclasses import replace

from corki.prompting import PromptContribution, PromptPhase, PromptRole, PromptSlot, PromptStore
from corki.protocol.items import ContextItem
from corki.protocol.tool_names import (
    NAMESPACE_WHITESPACE,
    has_namespace_description,
    split_tool_name,
)
from corki.protocol.tools import ToolSpec

KEY = "tools.deferred_namespaces"
MAX_FRAGMENT_BYTES = 4096
_PROMPTS = PromptStore()
_ROOT = "context/deferred_tools/"


def namespace_snapshot(specs: tuple[ToolSpec, ...]) -> dict[str, str]:
    namespaces: dict[str, str] = {}
    # Pick descriptions in captured registry/source order. Sorting leaf names
    # here changes which description wins in the default relaxed policy.
    for spec in specs:
        namespace, _ = split_tool_name(spec.name)
        if not spec.exposure.is_deferred or namespace is None:
            continue
        previous = namespaces.setdefault(namespace, "")
        description = spec.namespace_description or ""
        if not has_namespace_description(previous) and has_namespace_description(description):
            namespaces[namespace] = description
    # Rust lines() separates LF/CRLF only. Trim after taking the first line.
    return {
        name: description.split("\n", 1)[0].strip(NAMESPACE_WHITESPACE)[:250]
        for name, description in sorted(namespaces.items())
    }


def encode_snapshot(namespaces: dict[str, str]) -> str:
    return json.dumps({"version": 1, "namespaces": namespaces}, sort_keys=True, ensure_ascii=True)


def decode_snapshot(state: str | None) -> dict[str, str] | None:
    """Unknown historical versions are not evidence of additions or removals."""
    if state is None:
        return None
    try:
        value = json.loads(state)
    except (ValueError, TypeError, RecursionError):
        return None
    if (
        not isinstance(value, dict)
        or type(value.get("version")) is not int
        or value["version"] != 1
    ):
        return None
    namespaces = value.get("namespaces")
    if not isinstance(namespaces, dict) or any(
        not isinstance(name, str) or not isinstance(description, str)
        for name, description in namespaces.items()
    ):
        return None
    return namespaces


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def render_namespaces(current: dict[str, str], previous: dict[str, str] | None = None) -> str:
    if current == previous or not current and not previous:
        return ""
    # Codex does not persist an empty section: the next nonempty catalog is full.
    if not previous:
        groups = (("full", current),)
    else:
        groups = (
            ("added", {k: v for k, v in current.items() if previous.get(k) != v}),
            ("removed", {k: v for k, v in previous.items() if k not in current}),
        )
    groups = tuple(
        (_PROMPTS.render(_ROOT + label), entries) for label, entries in groups if entries
    )
    empty = _PROMPTS.render(_ROOT + "empty") if not current else ""
    # Include the bundled Markdown resource's final newline in the byte budget.
    wrapper = _PROMPTS.render(_ROOT + "fragment", body="")
    fixed = (
        len(wrapper.encode())
        + len(empty.encode())
        + sum(len(label.encode()) + 64 for label, _ in groups)
    )
    remaining = max(0, MAX_FRAGMENT_BYTES - fixed)
    body = ""
    for label, entries in groups:
        body += label
        omitted = 0
        for name, description in sorted(entries.items()):
            entry = (
                "- " + _escape(name) + (": " + _escape(description) if description else "") + "\n"
            )
            size = len(entry.encode())
            if size <= remaining:
                remaining -= size
                body += entry
            else:
                omitted += 1
        if omitted:
            body += _PROMPTS.render(_ROOT + "omitted", count=str(omitted))
    return _PROMPTS.render(_ROOT + "fragment", body=body + empty)


class DeferredToolsContextContributor:
    def step_contributions(
        self, *, tool_specs: tuple[ToolSpec, ...]
    ) -> tuple[PromptContribution, ...]:
        namespaces = namespace_snapshot(tool_specs)
        full = render_namespaces(namespaces)
        return (
            PromptContribution(
                key=KEY,
                content_kind="tools.deferred_namespaces",
                template_name=_ROOT + "catalog" if full else None,
                role=PromptRole.DEVELOPER,
                slot=PromptSlot.EXTENSIONS,
                order=-10,
                phase=PromptPhase.WORLD_STATE,
                variables={"content": full.removesuffix("\n")},
                snapshot_state=encode_snapshot(namespaces),
            ),
        )


def render_update(item: ContextItem, previous: ContextItem | None) -> ContextItem:
    current = decode_snapshot(item.snapshot_state)
    if current is None:
        if item.content:
            # Preserve legacy/unknown-version messages without interpreting metadata.
            return item
        current = {}
    prior = decode_snapshot(previous.snapshot_state) if previous is not None else None
    return replace(
        item,
        content=render_namespaces(current, prior),
        snapshot_content=render_namespaces(current),
        snapshot_state=encode_snapshot(current),
    )
