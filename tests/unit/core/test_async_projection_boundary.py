"""A checkpoint plan must not acknowledge completions outside its snapshot."""

import asyncio
import json

import pytest

from corki.core.async_post_hooks import AsyncPostHooks
from corki.core.hook_context import prepare_context
from corki.core.stop_hooks import StopCommand


@pytest.mark.parametrize("event", ["PreToolUse", "PostToolUse"])
@pytest.mark.parametrize("restored_reordered", [False, True])
def test_completion_during_plan_commit_remains_pending(event, restored_reordered):
    async def scenario():
        hooks = AsyncPostHooks(event_name=event)
        hooks._recovered = True
        command = StopCommand("hook", "hash", "unused", 10, asynchronous=True)

        def publish(name):
            raw = {
                "exit_code": 0,
                "stdout": json.dumps(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": event,
                            "additionalContext": name,
                        }
                    }
                ),
            }
            hooks.owner.publish(hooks.prefix + name, (command, "thread", "turn", None, raw))

        class Repository:
            def __init__(self):
                self.items, self.batches = {}, {}

            async def load_items(self, thread):
                return tuple(self.items.values())

            async def load_hook_batch(self, thread, turn, key):
                return self.batches.get(key)

            async def save_hook_batch(self, thread, turn, key, value):
                self.batches[key] = (value, {})
                if key.startswith("async_hook_projection:"):
                    publish("late")

            async def append_items(self, thread, items):
                self.items.update((item.id, item) for item in items)

        class Sink:
            async def emit(self, event):
                pass

        repository = Repository()
        if restored_reordered:
            fragment = await prepare_context(
                repository,
                {"thread_id": "thread", "turn_id": "turn", "request_items": ()},
                hooks.prefix + "first:async",
                "first",
                command.additional_context_limit,
            )
            repository.batches[f"async_hook_projection:{event}:thread:turn:checkpoint"] = (
                {"version": 1, "item_ids": [str(fragment.id)]},
                {},
            )
            publish("late")
        publish("first")
        try:
            projected = await hooks.project_checkpoint(
                repository,
                Sink(),
                thread="thread",
                turn="turn",
                checkpoint_id="checkpoint",
            )
            assert [item.content for item in projected] == ["first"]
            assert [item.content for item in repository.items.values()] == ["first"]
            assert hooks.owner.peek()[0] == hooks.prefix + "late"
            assert hooks.receipt_prefix + hooks.prefix + "late" not in repository.batches
            await hooks.drain(repository, Sink(), active_thread="thread", active_turn="turn")
            assert [item.content for item in repository.items.values()] == ["first", "late"]
            assert hooks.owner.peek() is None
        finally:
            await hooks.owner.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("event", ["PreToolUse", "PostToolUse"])
def test_invalid_projection_does_not_partially_deliver(event):
    async def scenario():
        hooks = AsyncPostHooks(event_name=event)
        hooks._recovered = True
        command = StopCommand("hook", "hash", "unused", 10, asynchronous=True)
        execution = hooks.prefix + "first"
        writes = []

        class Repository:
            async def load_items(self, thread):
                return ()

            async def load_hook_batch(self, thread, turn, key):
                return {"version": 1, "item_ids": [str(fragment.id), "missing"]}, {}

            async def append_items(self, thread, items):
                writes.append("history")

            async def save_hook_batch(self, *args):
                writes.append("receipt")

        class Sink:
            async def emit(self, event):
                writes.append("event")

        repository = Repository()
        fragment = await prepare_context(
            repository,
            {"thread_id": "thread", "turn_id": "turn", "request_items": ()},
            execution + ":async",
            "first",
            0,
        )
        raw = {
            "exit_code": 0,
            "stdout": json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": event,
                        "additionalContext": "first",
                    }
                }
            ),
        }
        hooks.owner.publish(execution, (command, "thread", "turn", None, raw))
        try:
            with pytest.raises(ValueError):
                await hooks.project_checkpoint(
                    repository,
                    Sink(),
                    thread="thread",
                    turn="turn",
                    checkpoint_id="checkpoint",
                )
            assert writes == []
            assert hooks.owner.peek()[0] == execution
        finally:
            await hooks.owner.aclose()

    asyncio.run(scenario())
