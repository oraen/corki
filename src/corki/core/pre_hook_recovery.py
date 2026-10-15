"""Recover pure PreToolUse feedback, never command or handler execution."""

from uuid import NAMESPACE_URL, uuid5

from corki.core.hook_context import prepare_context
from corki.core.pre_tool_hooks import outcome
from corki.protocol.events import WarningEvent
from corki.protocol.items import UserMessageItem


async def recover_feedback(repository, thread_id, turn_id, events):
    prefix = f"pre_tool_use:{turn_id}:"
    records = await repository.load_hook_executions(thread_id, turn_id, prefix)
    if not records:
        return
    history = await repository.load_items(thread_id)
    existing_ids = {item.id for item in history}
    users = {
        item.id: item
        for item in history
        if isinstance(item, UserMessageItem) and item.turn_id == turn_id
    }
    pending, groups, positions = [], {}, set()
    for key, request, result in records:
        if result is None or str(uuid5(NAMESPACE_URL, key + ":additional_context")) in existing_ids:
            continue
        context = outcome(result)[4]
        if context is None:
            continue
        policy = request.get("feedback")
        if policy is None:
            await events.emit(
                WarningEvent(
                    thread_id,
                    turn_id,
                    "Legacy PreToolUse feedback has no saved budget/source; "
                    "it was not reconstructed",
                )
            )
            continue
        payload = request.get("payload", {})
        if (
            not isinstance(policy, dict)
            or type(policy.get("version")) is not int
            or policy["version"] != 1
            or type(policy.get("order")) is not int
            or policy["order"] < 0
            or type(policy.get("limit")) is not int
            or policy["limit"] < 0
            or "source_input_id" not in policy
            or not isinstance(payload, dict)
            or payload.get("hook_event_name") != "PreToolUse"
            or payload.get("turn_id") != str(turn_id)
            or not isinstance(payload.get("tool_use_id"), str)
            or not key.startswith(prefix + payload["tool_use_id"] + ":")
        ):
            raise ValueError("Invalid persisted PreToolUse feedback contract")
        source_id = policy.get("source_input_id")
        if source_id is not None and (not isinstance(source_id, str) or source_id not in users):
            raise ValueError("Invalid persisted PreToolUse feedback input owner")
        group = groups.setdefault(payload["tool_use_id"], len(groups))
        position = (group, policy["order"])
        if position in positions:
            raise ValueError("Invalid persisted PreToolUse feedback order: duplicate position")
        positions.add(position)
        pending.append((group, policy["order"], key, context, policy["limit"], source_id))
    items = []
    for _, _, key, context, limit, source_id in sorted(pending):
        state = {
            "thread_id": thread_id,
            "turn_id": turn_id,
            "request_items": (users[source_id],) if source_id is not None else (),
        }
        items.append(await prepare_context(repository, state, key, context, limit))
    if items:
        await repository.append_items(thread_id, tuple(items))
