"""Hermes background_review._digest_history, expressed in Corki semantic items."""

from dataclasses import replace

from corki.context import active_history
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, UserMessageItem


def routed_snapshot(snapshot, tail=24):
    items = active_history(snapshot.items)
    groups = []
    for item in items:
        # One assistant message can contain text and multiple calls from one step.
        key = ("assistant", item.step_id) if hasattr(item, "step_id") else ("item", item.id)
        if groups and groups[-1][0] == key:
            groups[-1][1].append(item)
        else:
            groups.append((key, [item]))
    if len(groups) <= tail:
        return snapshot
    start = len(groups) - tail
    while start > 0 and isinstance(groups[start][1][0], ToolResultItem):
        start -= 1
    old = [item for _, group in groups[:start] for item in group]
    keep = tuple(item for _, group in groups[start:] for item in group)
    lines = []
    for item in old:
        if isinstance(item, UserMessageItem) and item.content:
            lines.append("USER: " + item.content.replace("\n", " ")[:300])
        elif isinstance(item, AssistantMessageItem) and item.content:
            lines.append("ASSISTANT: " + item.content.replace("\n", " ")[:200])
        elif isinstance(item, ToolCallItem):
            lines.append("ASSISTANT[tools: " + item.call.name + "]")
    digest = UserMessageItem(
        "[Earlier conversation digest — older turns summarised to bound the review's "
        "cold-write cost on the routed aux model. Recent turns follow verbatim below.]\n"
        + "\n".join(lines),
        keep[0].turn_id,
    )
    return replace(snapshot, items=(digest, *keep))
