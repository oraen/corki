"""Model-visible hook feedback, separate from diagnostic warnings."""

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from corki.context.hosted_output import truncate_output_text
from corki.protocol.ids import ItemId
from corki.protocol.items import ContextItem, ContextRole, UserMessageItem
from corki.protocol.truncation import TruncationPolicy


def _preview(text, tokens):
    truncated = truncate_output_text(text, TruncationPolicy("tokens", tokens))
    if truncated == text:
        return text
    return (
        f"Warning: truncated output (original token count: {(len(text.encode()) + 3) // 4})\n"
        f"Total output lines: {len(text.splitlines())}\n\n{truncated}"
    )


def _spill(text, limit):
    if limit == 0 or len(text.encode()) <= limit * 4:
        return text
    try:
        # Exclusive creation with mode 0600; never overwrite a user-selected path.
        descriptor, name = tempfile.mkstemp(prefix="corki-hook-", suffix=".txt")
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(text)
        footer = f"\n\nFull hook output saved to: {Path(name)}"
        return _preview(text, max(0, limit - (len(footer.encode()) + 3) // 4)) + footer
    except OSError:
        logging.getLogger(__name__).warning("Could not preserve full hook output", exc_info=True)
        return _preview(text, limit)


def context_item_id(key):
    return ItemId(str(uuid5(NAMESPACE_URL, key + ":additional_context")))


async def prepare_context(repository, state, key, text, limit):
    """Prepare a bounded fragment without publishing conversation history."""
    history = await repository.load_items(state["thread_id"])
    item_id = context_item_id(key)
    existing = next((item for item in history if item.id == item_id), None)
    if existing is not None:
        return existing
    task = asyncio.create_task(asyncio.to_thread(_spill, text, limit))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break  # Inspect the worker result without replacing an admitted cancellation.
    try:
        content = task.result()
    except Exception as error:
        if cancelled:
            raise asyncio.CancelledError from error
        raise
    if cancelled:
        raise asyncio.CancelledError
    source_input_id = next(
        (
            item.id
            for item in reversed(state["request_items"])
            if isinstance(item, UserMessageItem) and item.turn_id == state["turn_id"]
        ),
        None,
    )
    return ContextItem(
        key=key + ":additional_context",
        role=ContextRole.DEVELOPER,
        content=content,
        turn_id=state["turn_id"],
        id=item_id,
        source_input_id=source_input_id,
        content_kind="hooks.additional_context",
    )
