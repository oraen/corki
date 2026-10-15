"""Publish committed prompt-hook feedback only at harness boundaries."""

from dataclasses import replace

from corki.core.hook_context import prepare_context
from corki.core.prompt_hook_output import outcome
from corki.core.stop_hooks import _execution_request, restore_command
from corki.protocol.events import WarningEvent
from corki.protocol.items import UserMessageItem


async def _completed(repository, thread):
    history = await repository.load_items(thread)
    inputs = {
        f"user_prompt_submit:{item.turn_id}:{item.id}:": item
        for item in history
        if isinstance(item, UserMessageItem)
    }
    for source_turn, prefix in await repository.load_hook_batch_keys(thread, "user_prompt_submit:"):
        if prefix.endswith(":receipt"):
            continue
        saved = await repository.load_hook_batch(thread, source_turn, prefix)
        if saved is None:
            raise ValueError("Asynchronous prompt hook plan disappeared")
        batch, records = saved
        if type(batch.get("version")) is not int or batch["version"] != 1:
            raise ValueError("Invalid asynchronous prompt hook plan")
        source = inputs.get(prefix)
        # Inputs not admitted to history cannot acquire background context.
        if source is None:
            continue
        payload = batch["payload"]
        if (
            payload.get("turn_id") != str(source_turn)
            or payload.get("prompt") != source.content
            or payload.get("hook_event_name") != "UserPromptSubmit"
        ):
            raise ValueError("Asynchronous prompt hook identity mismatch")
        for entry in batch["commands"]:
            if type(entry.get("asynchronous")) is not bool:
                raise ValueError("Invalid asynchronous prompt mode")
            if not entry["asynchronous"]:
                continue
            command = restore_command(
                {**entry, "environment": tuple(tuple(p) for p in entry["environment"])}
            )
            key = prefix + command.key
            record = records.get(key)
            if record is None or record["result"] is None:
                continue
            if record["request"] != _execution_request(command, payload):
                raise ValueError("Asynchronous prompt request mismatch")
            receipt_key = "async_prompt_delivered:" + key
            delivered = await repository.load_hook_batch(thread, source_turn, receipt_key)
            if delivered is not None:
                value, executions = delivered
                if (
                    executions
                    or set(value) != {"version"}
                    or type(value["version"]) is not int
                    or value["version"] != 1
                ):
                    raise ValueError("Invalid asynchronous prompt delivery receipt")
            yield source_turn, source, command, key, record["result"], delivered is not None


async def drain(repository, thread, turn, events, *, selected_keys=None):
    history = await repository.load_items(thread)
    async for source_turn, source, command, key, raw, delivered in _completed(repository, thread):
        if delivered or (selected_keys is not None and key not in selected_keys):
            continue
        parsed = outcome(raw, control=False)
        if parsed.context and parsed.context.strip():
            context = await prepare_context(
                repository,
                {
                    "thread_id": thread,
                    "turn_id": source_turn,
                    "request_items": history,
                },
                key,
                parsed.context,
                command.additional_context_limit,
            )
            await repository.append_items(thread, (replace(context, source_input_id=source.id),))
        for entry in parsed.entries:
            if entry.kind == "warning":
                await events.emit(WarningEvent(thread, turn, entry.text))
        await repository.save_hook_batch(
            thread, source_turn, "async_prompt_delivered:" + key, {"version": 1}
        )


async def project_checkpoint(repository, thread, turn, events, *, checkpoint_id):
    """Freeze the selected executions before acknowledging any delivery."""
    from corki.core.hook_context import context_item_id
    from corki.protocol.items import ContextItem

    if not isinstance(checkpoint_id, str) or not checkpoint_id:
        raise ValueError("Invalid asynchronous prompt checkpoint identity")
    key = f"async_prompt_projection:{thread}:{turn}:{checkpoint_id}"
    available = {entry[3]: entry async for entry in _completed(repository, thread)}
    saved = await repository.load_hook_batch(thread, turn, key)
    if saved is None:
        selected = [identity for identity, entry in available.items() if not entry[5]]
        await repository.save_hook_batch(thread, turn, key, {"version": 1, "keys": selected})
    else:
        plan, executions = saved
        selected = plan.get("keys")
        if (
            executions
            or set(plan) != {"version", "keys"}
            or type(plan.get("version")) is not int
            or plan["version"] != 1
            or not isinstance(selected, list)
            or any(not isinstance(value, str) for value in selected)
            or len(selected) != len(set(selected))
        ):
            raise ValueError("Invalid asynchronous prompt checkpoint projection")
    if any(identity not in available for identity in selected):
        raise ValueError("Missing projected asynchronous prompt execution")
    await drain(repository, thread, turn, events, selected_keys=set(selected))
    history = {item.id: item for item in await repository.load_items(thread)}
    items = []
    for identity in selected:
        source_turn, source, _, _, raw, _ = available[identity]
        parsed = outcome(raw, control=False)
        if not parsed.context or not parsed.context.strip():
            continue
        item = history.get(context_item_id(identity))
        if (
            not isinstance(item, ContextItem)
            or item.key != identity + ":additional_context"
            or item.content_kind != "hooks.additional_context"
            or item.turn_id != source_turn
            or item.source_input_id != source.id
        ):
            raise ValueError("Missing or invalid projected asynchronous prompt context")
        items.append(item)
    return tuple(items)
