"""Session-owned Post commands; only sampling boundaries publish their feedback."""

import json
import logging
from dataclasses import replace
from pathlib import Path

from corki.core.async_hooks import AsyncHooks
from corki.core.hook_context import context_item_id, prepare_context
from corki.core.post_tool_hooks import outcome
from corki.core.stop_hooks import StopCommand, _execution_request, run_command
from corki.protocol.events import WarningEvent
from corki.protocol.items import ContextItem, UserMessageItem


class AsyncPostHooks:
    def __init__(self, *, event_name="PostToolUse", limiter=None):
        self.event_name = event_name
        self.prefix = {"PreToolUse": "pre_tool_use:", "PostToolUse": "post_tool_use:"}[event_name]
        self.receipt_prefix = (
            "async_pre_delivered:" if event_name == "PreToolUse" else "async_post_delivered:"
        )
        self.owner = AsyncHooks(limiter=limiter)
        self._recovered = False

    async def recover(self, repository, thread):
        if self._recovered:
            return
        users = {
            str(item.id): str(item.turn_id)
            for item in await repository.load_items(thread)
            if isinstance(item, UserMessageItem)
        }
        pending = []
        for turn, prefix in await repository.load_hook_batch_keys(thread, self.prefix):
            saved = await repository.load_hook_batch(thread, turn, prefix)
            if saved is None:
                raise ValueError("Persisted PostToolUse batch disappeared")
            batch, records = saved
            payload, commands = batch.get("payload"), batch.get("commands")
            source = batch.get("source_input_id")
            if (
                not prefix.startswith(f"{self.prefix}{thread}:{turn}:")
                or not isinstance(payload, dict)
                or payload.get("turn_id") != turn
                or payload.get("hook_event_name") != self.event_name
                or not isinstance(commands, list)
                or "source_input_id" not in batch
                or (
                    source is not None
                    and (not isinstance(source, str) or users.get(source) != turn)
                )
            ):
                raise ValueError("Invalid persisted asynchronous PostToolUse batch")
            for index, entry in enumerate(commands):
                if (
                    not isinstance(entry, dict)
                    or type(entry.get("asynchronous", False)) is not bool
                ):
                    raise ValueError("Invalid persisted PostToolUse mode")
                if not entry.get("asynchronous", False):
                    continue
                if (
                    type(entry.get("additional_context_limit")) is not int
                    or entry["additional_context_limit"] < 0
                ):
                    raise ValueError("Invalid persisted asynchronous PostToolUse budget")
                command = StopCommand(**entry)
                key = prefix + str(index)
                record = records.get(key)
                # Unclaimed and unknown effects are never rerun on startup.
                if record is None or record["result"] is None:
                    continue
                expected = json.loads(json.dumps(_execution_request(command, payload)))
                if record["request"] != expected:
                    raise ValueError("Persisted asynchronous PostToolUse request mismatch")
                delivered = await repository.load_hook_batch(
                    thread, turn, self.receipt_prefix + key
                )
                if delivered is not None:
                    receipt, executions = delivered
                    if (
                        not isinstance(receipt, dict)
                        or set(receipt) != {"version"}
                        or type(receipt["version"]) is not int
                        or receipt["version"] != 1
                        or executions
                    ):
                        raise ValueError("Invalid asynchronous PostToolUse delivery receipt")
                    continue
                pending.append((key, (command, thread, turn, source, record["result"])))
        for key, result in pending:
            self.owner.publish(key, result)
        self._recovered = True

    async def schedule(
        self,
        key,
        command,
        payload,
        source,
        *,
        repository,
        shell,
        environment,
        fresh,
        records,
        thread,
        turn,
    ):
        request = _execution_request(command, payload)
        if self.owner.owns(key, request):
            return
        if not fresh:
            record = records.get(key)
            raw = record["result"] if record is not None else None
            if raw is not None:
                self.owner.publish(key, (command, thread, turn, source, raw))
            return
        raw = await repository.claim_hook_execution(thread, turn, key, request)
        if raw is not None:
            self.owner.publish(key, (command, thread, turn, source, raw))
            return
        child_environment = {**environment, **dict(command.environment)}

        async def execute():
            try:
                raw = await run_command(
                    command,
                    payload,
                    shell=shell,
                    cwd=Path(payload["cwd"]),
                    environment=child_environment,
                )
            except (OSError, ValueError, TimeoutError) as error:
                raw = {"error": f"PostToolUse failed: {type(error).__name__}: {error}"}
            await repository.complete_hook_execution(thread, turn, key, request, raw)
            return command, thread, turn, source, raw

        self.owner.start(key, request, execute)

    async def project_checkpoint(self, repository, events, *, thread, turn, checkpoint_id):
        """Journal feedback selected for a frozen sampling checkpoint before delivery."""
        if not isinstance(checkpoint_id, str) or not checkpoint_id:
            raise ValueError("Invalid asynchronous hook checkpoint identity")
        await self.recover(repository, thread)
        history = {str(item.id): item for item in await repository.load_items(thread)}
        key = f"async_hook_projection:{self.event_name}:{thread}:{turn}:{checkpoint_id}"
        saved = await repository.load_hook_batch(thread, turn, key)
        if saved is None:
            selected = []
            for execution_key, (
                command,
                source_thread,
                source_turn,
                _source,
                raw,
            ) in self.owner.pending():
                if self.event_name == "PreToolUse":
                    from corki.core.async_pre_output import outcome as pre_outcome

                    context = pre_outcome(raw)[2]
                else:
                    context = outcome(raw, control=False)[2]
                if not context:
                    continue
                fragment = await prepare_context(
                    repository,
                    {"thread_id": source_thread, "turn_id": source_turn, "request_items": ()},
                    execution_key + ":async",
                    context,
                    command.additional_context_limit,
                )
                if str(fragment.id) not in history:
                    selected.append(str(fragment.id))
            await repository.save_hook_batch(
                thread, turn, key, {"version": 1, "item_ids": selected}
            )
        else:
            plan, executions = saved
            if not isinstance(plan, dict):
                raise ValueError("Invalid asynchronous hook checkpoint projection")
            selected = plan.get("item_ids")
            if (
                executions
                or set(plan) != {"version", "item_ids"}
                or type(plan.get("version")) is not int
                or plan["version"] != 1
                or not isinstance(selected, list)
                or any(not isinstance(value, str) for value in selected)
                or len(selected) != len(set(selected))
            ):
                raise ValueError("Invalid asynchronous hook checkpoint projection")
        # Validate the entire selection before acknowledging even its first entry.
        # Pending results have no history item yet; their deterministic identities
        # are derived from validated completed executions, not from the saved plan.
        available = {
            identity
            for identity, item in history.items()
            if isinstance(item, ContextItem)
            and item.content_kind == "hooks.additional_context"
            and item.key.startswith(self.prefix)
            and item.key.endswith(":async:additional_context")
            and identity == str(context_item_id(item.key.removesuffix(":additional_context")))
        }
        for execution_key, (_, source_thread, _, _, raw) in self.owner.pending():
            if self.event_name == "PreToolUse":
                from corki.core.async_pre_output import outcome as pre_outcome

                context = pre_outcome(raw)[2]
            else:
                context = outcome(raw, control=False)[2]
            if source_thread == thread and execution_key.startswith(self.prefix) and context:
                available.add(str(context_item_id(execution_key + ":async")))
        if any(identity not in available for identity in selected):
            raise ValueError("Missing or invalid projected asynchronous hook context")
        await self.drain(
            repository,
            events,
            active_thread=thread,
            active_turn=turn,
            projected_ids=set(selected),
        )
        history = {str(item.id): item for item in await repository.load_items(thread)}
        items = []
        for identity in selected:
            item = history.get(identity)
            if (
                item is None
                or not getattr(item, "key", "").startswith(self.prefix)
                or not item.key.endswith(":async:additional_context")
            ):
                raise ValueError("Missing or invalid projected asynchronous hook context")
            items.append(item)
        return tuple(items)

    async def drain(self, repository, events, *, active_thread, active_turn, projected_ids=None):
        for entry in self.owner.pending():
            key, (command, thread, turn, source, raw) = entry
            if self.event_name == "PreToolUse":
                from corki.core.async_pre_output import outcome as pre_outcome

                _, _, context, warning, error = pre_outcome(raw)
            else:
                _, _, context, warning, error = outcome(raw, control=False)
            if error:
                logging.getLogger(__name__).warning("Async Post hook %s: %s", key, error)
            if context:
                fragment = await prepare_context(
                    repository,
                    {"thread_id": thread, "turn_id": turn, "request_items": ()},
                    key + ":async",
                    context,
                    command.additional_context_limit,
                )
                fragment = replace(fragment, source_input_id=source)
                if projected_ids is not None and str(fragment.id) not in projected_ids:
                    continue
                await repository.append_items(thread, (fragment,))
            elif projected_ids is not None:
                # Diagnostic-only results were not selected for this frozen request.
                continue
            if warning is not None:
                await events.emit(WarningEvent(active_thread, active_turn, warning))
            # A cancelled append/emit retains the queue entry and stable context
            # ID. Never acknowledge before publishing its durable model feedback.
            await repository.save_hook_batch(
                thread, turn, self.receipt_prefix + key, {"version": 1}
            )
            if projected_ids is None:
                self.owner.acknowledge(key)
            else:
                self.owner.acknowledge_selected(key)
