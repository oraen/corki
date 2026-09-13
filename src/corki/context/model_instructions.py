"""Restore comparison identity independently from retained model instructions."""

import json
import logging

from corki.protocol.items import ContextItem

_LOGGER = logging.getLogger(__name__)


def restored_model(item: ContextItem | None) -> str | None:
    if item is None or item.snapshot_state is None:
        return None
    try:
        state = json.loads(item.snapshot_state)
        if not isinstance(state, dict) or not isinstance(state.get("model"), str):
            raise ValueError("invalid model snapshot")
        return state["model"]
    except (ValueError, RecursionError):
        _LOGGER.warning("Failed to restore model snapshot; treating it as unknown")
        return None


def has_rendering_state(item: ContextItem) -> bool:
    if restored_model(item) is None:
        return False
    state = json.loads(item.snapshot_state)
    return "base_model" in state and (
        state["base_model"] is None or isinstance(state["base_model"], str)
    )
