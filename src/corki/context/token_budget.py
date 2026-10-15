"""Identity of an explicit TokenBudget window, separate from conversation text."""

from dataclasses import dataclass, replace
from uuid import NAMESPACE_URL, uuid5

from corki.context.builder import ContextSnapshot
from corki.protocol.items import CompactionItem, ContextItem, ContextRole


@dataclass(frozen=True, slots=True)
class BudgetStatus:
    remaining: int
    limit_reached: bool


def window_identities(stored, thread_id) -> list[str]:
    initial = str(uuid5(NAMESPACE_URL, f"corki:context-window:{thread_id}"))
    return [initial, *(str(item.id) for item in stored if isinstance(item, CompactionItem))]


def with_window_context(
    snapshot: ContextSnapshot, stored, thread_id, turn_id, guidance=None
) -> ContextSnapshot:
    windows = window_identities(stored, thread_id)
    initial = windows[0]
    lines = [
        "Agent name: /root",
        f"First context window id: {initial}",
        f"Current context window id: {windows[-1]}",
    ]
    if len(windows) > 1:
        lines.append(f"Previous context window id: {windows[-2]}")
    item = ContextItem(
        "context_window",
        ContextRole.DEVELOPER,
        "<context_window>\n" + "\n".join(lines) + "\n</context_window>",
        turn_id,
        content_kind="token_budget.context_window",
        separate_message=True,
    )
    items = tuple(
        i for i in snapshot.items if i.key not in ("context_window", "context_window_guidance")
    )
    if guidance is not None and guidance.strip():
        items += (
            ContextItem(
                "context_window_guidance",
                ContextRole.DEVELOPER,
                f"<context_window_guidance>\n{guidance}\n</context_window_guidance>",
                turn_id,
                content_kind="token_budget.context_window_guidance",
            ),
        )
    return replace(snapshot, items=(*items, item))
