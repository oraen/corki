"""Recover saved PostToolUse context without dispatching tools or hook commands."""

from corki.core.hook_context import prepare_context
from corki.core.post_tool_hooks import outcome
from corki.protocol.items import UserMessageItem


async def recover_feedback(repository, thread_id, turn_id, *, checkpoint_id=None):
    history = await repository.load_items(thread_id)
    users = {
        item.id: item
        for item in history
        if isinstance(item, UserMessageItem) and item.turn_id == turn_id
    }
    pending = []
    for result in await repository.load_turn_tool_outcomes(thread_id, turn_id):
        prefix = f"post_tool_use:{thread_id}:{turn_id}:{result.call_id}:"
        saved = await repository.load_hook_batch(thread_id, turn_id, prefix)
        if saved is None:
            continue
        batch, records = saved
        source = batch.get("source_input_id")
        payload = batch.get("payload")
        commands = batch.get("commands")
        if (
            "source_input_id" not in batch
            or (source is not None and (not isinstance(source, str) or source not in users))
            or not isinstance(payload, dict)
            or payload.get("hook_event_name") != "PostToolUse"
            or payload.get("turn_id") != str(turn_id)
            or not isinstance(commands, list)
        ):
            raise ValueError("Invalid persisted PostToolUse feedback contract")
        parent = batch.get("parent_call_id")
        unfinished_parent = False
        if parent is not None:
            if not isinstance(parent, str) or not parent or parent == str(result.call_id):
                raise ValueError("Invalid persisted PostToolUse parent identity")
            unfinished_parent = not await repository.code_mode_parent_finished(thread_id, parent)
        for index, command in enumerate(commands):
            if (
                not isinstance(command, dict)
                or type(command.get("additional_context_limit")) is not int
                or command["additional_context_limit"] < 0
            ):
                raise ValueError("Invalid persisted PostToolUse feedback budget")
            key = prefix + str(index)
            asynchronous = command.get("asynchronous", False)
            if type(asynchronous) is not bool:
                raise ValueError("Invalid persisted PostToolUse execution mode")
            record = records.get(key)
            raw = record["result"] if record is not None else None
            if unfinished_parent and not asynchronous:
                blocked, feedback = (
                    (True, "PostToolUse outcome unknown; not repeated.")
                    if raw is None
                    else outcome(raw)[:2]
                )
                if blocked:
                    pending.append(
                        (
                            key + ":recovered_block",
                            f"Recovered PostToolUse feedback for nested call {result.call_id} "
                            f"in unfinished script {parent}. "
                            f"The script outcome remains unknown.\n{feedback}",
                            command["additional_context_limit"],
                            source,
                        )
                    )
            if record is None or record["result"] is None:
                continue
            context = outcome(record["result"], control=not asynchronous)[2]
            if context:
                pending.append(
                    (
                        key + ":async" if asynchronous else key,
                        context,
                        command["additional_context_limit"],
                        source,
                    )
                )
    # Validate all batches before publishing any context; retain the original ID
    # even if compaction has removed the fragment from the model-visible window.
    items = []
    for key, context, limit, source in pending:
        state = {
            "thread_id": thread_id,
            "turn_id": turn_id,
            "request_items": (users[source],) if source is not None else (),
        }
        items.append(await prepare_context(repository, state, key, context, limit))
    existing = {item.id for item in history}
    selected_ids = [str(item.id) for item in items if item.id not in existing]
    if checkpoint_id is not None:
        if not isinstance(checkpoint_id, str) or not checkpoint_id:
            raise ValueError("Invalid PostToolUse recovery checkpoint identity")
        key = f"post_feedback_projection:{thread_id}:{turn_id}:{checkpoint_id}"
        saved = await repository.load_hook_batch(thread_id, turn_id, key)
        if saved is None:
            # Write intent before history. Repeating this checkpoint can then
            # recover an append that committed before checkpoint replacement.
            await repository.save_hook_batch(
                thread_id, turn_id, key, {"version": 1, "item_ids": selected_ids}
            )
        else:
            plan, executions = saved
            selected_ids = plan.get("item_ids")
            available = {str(item.id) for item in items}
            if (
                executions
                or type(plan.get("version")) is not int
                or plan["version"] != 1
                or not isinstance(selected_ids, list)
                or any(
                    not isinstance(value, str) or value not in available for value in selected_ids
                )
                or len(set(selected_ids)) != len(selected_ids)
            ):
                raise ValueError("Invalid PostToolUse recovery projection")
    if items:
        await repository.append_items(thread_id, tuple(items))
    selected = set(selected_ids)
    return tuple(item for item in items if str(item.id) in selected)
