"""Recovery rejects malformed receipts rather than silently dropping feedback."""

import asyncio
import json
from dataclasses import asdict

import pytest

from corki.core.async_post_hooks import AsyncPostHooks
from corki.core.stop_hooks import StopCommand, _execution_request


@pytest.mark.parametrize("version", [True, 1.0, "1", None, 0, 2])
def test_delivery_receipt_requires_integer_version(version):
    async def scenario():
        command = StopCommand("hook", "hash", "unused", 10, asynchronous=True)
        payload = {"turn_id": "turn", "hook_event_name": "PostToolUse"}
        prefix = "post_tool_use:thread:turn:call:"
        request = json.loads(json.dumps(_execution_request(command, payload)))

        class Repository:
            async def load_items(self, thread):
                return ()

            async def load_hook_batch_keys(self, thread, kind):
                return (("turn", prefix),)

            async def load_hook_batch(self, thread, turn, key):
                if key.startswith("async_post_delivered:"):
                    return {"version": version}, {}
                return (
                    {"payload": payload, "commands": [asdict(command)], "source_input_id": None},
                    {
                        prefix + "0": {
                            "request": request,
                            "result": {"exit_code": 0, "stdout": "{}"},
                        }
                    },
                )

        hooks = AsyncPostHooks()
        with pytest.raises(ValueError, match="delivery receipt"):
            await hooks.recover(Repository(), "thread")
        assert not hooks._recovered
        assert hooks.owner.peek() is None
        await hooks.owner.aclose()

    asyncio.run(scenario())
