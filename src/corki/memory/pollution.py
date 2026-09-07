"""Best-effort policy boundary shared by startup, model items and tool execution."""

import logging

from corki.protocol.items import HostedToolItem, ToolCallItem, ToolResultItem


def has_external_context(item) -> bool:
    return (
        isinstance(item, (HostedToolItem, ToolCallItem, ToolResultItem))
        and item.contains_external_context
    )


async def mark_polluted(repository, settings, thread_id) -> None:
    if not (
        settings.memories_enabled
        and settings.memories_disable_on_external_context
        and repository is not None
    ):
        return
    try:
        await repository.mark_thread_mode(thread_id, "polluted")
    except Exception as exc:  # CancelledError remains control flow.
        logging.getLogger(__name__).warning(
            "Memory pollution mark failed (%s); continuing the turn", type(exc).__name__
        )
