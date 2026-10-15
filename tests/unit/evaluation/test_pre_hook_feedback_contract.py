import asyncio

import pytest

from corki.core.pre_hook_recovery import recover_feedback
from corki.protocol.ids import ThreadId, new_turn_id
from corki.protocol.items import UserMessageItem
from corki.storage.sqlite import SQLiteSessionRepository


@pytest.mark.parametrize(
    "damage",
    [
        "version",
        "order",
        "limit",
        "owner",
        "missing_owner",
        "event",
        "turn",
        "call",
        "duplicate_order",
    ],
)
def test_invalid_contract_cannot_partially_publish_valid_feedback(tmp_path, damage):
    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "facts.db")
        thread, turn = ThreadId("thread"), new_turn_id()
        user = UserMessageItem("original", turn)
        await repository.create_thread(thread, tmp_path)
        await repository.append_items(thread, (user,))

        class Events:
            async def emit(self, event):
                raise AssertionError("malformed v1 is not a legacy warning")

        try:
            for index in range(2):
                key = f"pre_tool_use:{turn}:call:{index}"
                request = {
                    "payload": {
                        "hook_event_name": "PreToolUse",
                        "turn_id": str(turn),
                        "tool_use_id": "call",
                    },
                    "feedback": {
                        "version": 1,
                        "order": index,
                        "limit": 2500,
                        "source_input_id": user.id,
                    },
                }
                if index == 1:
                    if damage in {"version", "order", "limit"}:
                        request["feedback"][damage] = True
                    elif damage == "owner":
                        request["feedback"]["source_input_id"] = "unrelated"
                    elif damage == "missing_owner":
                        del request["feedback"]["source_input_id"]
                    elif damage == "duplicate_order":
                        request["feedback"]["order"] = 0
                    else:
                        field = {
                            "event": "hook_event_name",
                            "turn": "turn_id",
                            "call": "tool_use_id",
                        }[damage]
                        request["payload"][field] = "unrelated"
                await repository.claim_hook_execution(thread, turn, key, request)
                await repository.complete_hook_execution(
                    thread,
                    turn,
                    key,
                    request,
                    {
                        "exit_code": 0,
                        "stdout": '{"hookSpecificOutput":{"additionalContext":"feedback"}}',
                    },
                )
            facts = await repository.load_hook_executions(thread, turn, f"pre_tool_use:{turn}:")
            with pytest.raises(ValueError, match="persisted PreToolUse"):
                await recover_feedback(repository, thread, turn, Events())
            assert await repository.load_items(thread) == (user,)
            assert (
                await repository.load_hook_executions(thread, turn, f"pre_tool_use:{turn}:")
                == facts
            )
        finally:
            await repository.close()

    asyncio.run(scenario())
