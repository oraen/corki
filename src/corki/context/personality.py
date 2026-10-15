"""Typed personality differences, distinct from model-switch instructions."""

import json
import logging
from dataclasses import replace

from corki.protocol.items import ContextItem

KEY = "personality"
_LOGGER = logging.getLogger(__name__)


def _previous_snapshot(previous: ContextItem):
    if previous.snapshot_state in (None, "personality.legacy_unknown"):
        return None
    try:
        old = json.loads(previous.snapshot_state)
        if (
            not isinstance(old, dict)
            or not isinstance(old.get("model"), str)
            or old.get("personality") not in (None, "none", "friendly", "pragmatic")
        ):
            raise ValueError("invalid personality snapshot")
        return {"model": old["model"], "personality": old.get("personality")}
    except (ValueError, RecursionError):
        # Comparison metadata is not an execution ledger. Preserve the row,
        # but use the same Unknown fallback as a missing legacy typed state.
        _LOGGER.warning("Failed to restore personality snapshot; treating it as unknown")
        return None


def render_update(
    item: ContextItem, previous: ContextItem | None, previous_model: str | None = None
) -> ContextItem:
    if item.snapshot_state is None:
        return replace(item, content="", snapshot_content=item.content)
    state = json.loads(item.snapshot_state)
    if previous is not None:
        old = _previous_snapshot(previous)
        if old is None:
            # Native legacy fallback uses the accepted model and an unset
            # selection when no reference context exists. Never infer either
            # from the old fragment's prose or the current Thread defaults.
            emit = previous_model == state["model"] and state["personality"] is not None
        else:
            emit = old["model"] == state["model"] and old["personality"] != state["personality"]
    else:
        emit = not state["baked"] and previous_model in (None, state["model"])
    text = (
        "<personality_spec> The user has requested a new communication style. "
        "Future messages should adhere to the following personality: \n"
        f"{item.content} </personality_spec>"
        if emit and item.content
        else ""
    )
    return replace(item, content=text, snapshot_content=item.content)


def render_history(item: ContextItem, previous: ContextItem | None) -> ContextItem:
    """Project old raw snapshots without discarding their retained message."""
    if _previous_snapshot(item) is None:
        # A corrupt comparison snapshot cannot tell us how to reconstruct a
        # diff. Its existing message is still history, not a failed execution.
        return item
    state = json.loads(item.snapshot_state)
    if not isinstance(state.get("baked"), bool):
        # Older comparison-only formats do not carry rendering information.
        return item
    return render_update(item, previous)
