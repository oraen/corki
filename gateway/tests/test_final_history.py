"""Management history shows committed final answers, including unphased models."""

import asyncio

import pytest
from corki_gateway.history import history
from corki_gateway.store import Store

from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id
from corki.sessions.models import TurnRecord, TurnStatus
from corki.storage import SQLiteSessionRepository


@pytest.mark.parametrize("channel", ["wechat", "feishu"])
def test_final_answers_hide_intermediate_and_live_text(tmp_path, channel):
    async def scenario():
        store = Store(tmp_path)
        identity = store.save_connection(
            {
                "channel": channel,
                "name": "Fixture",
                "values": ({"app_id": "cli_0123456789abcdef", "app_secret": "fixture"}
                           if channel == "feishu" else {}),
            }
        )
        thread = store.admit(identity, {
            "id": "fixture", "sender": "reader", "conversation": "reader", "text": "Question",
        }, str(tmp_path / "main/default"))
        repo = SQLiteSessionRepository(tmp_path / "sessions/corki.db")
        await repo.create_thread(thread, tmp_path)
        items = (
            UserMessageItem("Question", "turn"),
            AssistantMessageItem(
                "Planning the next step", "turn", new_step_id(), phase="commentary"
            ),
            AssistantMessageItem("Checking the result", "turn", new_step_id()),
            AssistantMessageItem("Finished", "turn", new_step_id()),
        )
        await repo.append_items(thread, items)
        await repo.save_turn(TurnRecord("turn", thread, TurnStatus.RUNNING, "Question"))
        live = {thread: {"transient": {"id": "transient", "role": "assistant", "content": "Draft"}}}
        view = history(store, identity, {}, live)
        assert [m["content"] for m in view["messages"]] == ["Question"]
        assert view["active"]
        own = history(store, identity, {"sender": "reader"}, live)
        assert own["thread"] == thread
        assert own["sessions"] == []
        missing = history(store, identity, {"sender": "another-user"}, live)
        assert missing["thread"] is None
        assert missing["messages"] == []
        await repo.save_turn(
            TurnRecord("turn", thread, TurnStatus.COMPLETED, "Question", "Finished")
        )
        view = history(store, identity, {}, {})
        assert [m["content"] for m in view["messages"]] == ["Question", "Finished"]
        assert view["messages"][-1]["id"] == items[-1].id
        # Pagination must not make earlier unphased commentary look like a final answer.
        await repo.append_items(thread, tuple(UserMessageItem(str(i), "later") for i in range(120)))
        latest = history(store, identity, {}, {})
        older = history(store, identity, {"before": latest["before"], "thread": thread}, {})
        assert [m["content"] for m in older["messages"] if m["role"] == "assistant"] == ["Finished"]
        assert len(await repo.load_items(thread)) == 124  # Display filtering never deletes data.

    asyncio.run(scenario())
