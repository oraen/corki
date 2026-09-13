"""Previous accepted model settings, kept out of prompts and memory evidence."""

import json

from corki.protocol.items import ContextItem, ContextRole

KEY = "harness.previous_model"


def model_snapshot(info, turn_id):
    return ContextItem(
        KEY,
        ContextRole.DEVELOPER,
        "",
        turn_id,
        snapshot_state=json.dumps(
            {
                "model": info.model,
                "comp_hash": info.comp_hash,
            },
            separators=(",", ":"),
        ),
    )


def previous_model(history):
    # Unlike world-state rendering, model provenance survives compaction windows.
    for item in reversed(history):
        if isinstance(item, ContextItem) and item.key == KEY and item.is_snapshot_only:
            try:
                value = json.loads(item.snapshot_state)
                if not isinstance(value, dict) or not isinstance(value.get("model"), str):
                    return None
                if value.get("comp_hash") is not None and not isinstance(value["comp_hash"], str):
                    return None
                return value
            except (ValueError, TypeError):
                return None
    return None


def transition_target(
    history, current, lookup, *, active_tokens, usable_tokens, auto_limit, body_scope
):
    previous = previous_model(history)
    if previous is None:
        return None
    old = lookup(previous["model"])
    if (
        previous.get("comp_hash") is not None
        and current.comp_hash is not None
        and previous["comp_hash"] != current.comp_hash
    ):
        return old, "comp_hash_changed"
    old_window = old.resolved_context_window
    if old_window is None or old.model == current.model:
        return None
    old_usable = old_window * old.effective_context_window_percent // 100
    reached = active_tokens >= usable_tokens or (not body_scope and active_tokens > auto_limit)
    if old_usable > usable_tokens and reached:
        return old, "model_downshift"
    return None
