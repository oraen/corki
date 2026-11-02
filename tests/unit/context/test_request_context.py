"""Host request prefixes survive compaction without becoming durable conversation items."""

import asyncio
from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.context import ContextSnapshot, ContextWindowManager
from corki.context.tokens import estimate_request_tokens
from corki.models import ModelCompleted, ModelError
from corki.protocol.ids import new_thread_id, new_turn_id
from corki.protocol.items import AssistantMessageItem, ContextItem, ContextRole, UserMessageItem
from corki.sessions.models import ContextUsage
from corki.storage import SQLiteSessionRepository


def prefix(text="Use business language"):
    return ContextItem("fixture.reply", ContextRole.DEVELOPER, text, "host", separate_message=True)


class Model:
    def __init__(self):
        self.requests = []
        self.checked = []

    async def stream(self, request):
        self.requests.append(request)
        yield ModelCompleted(
            (AssistantMessageItem("compact summary", request.items[-1].turn_id, "s"),)
        )

    async def request_byte_budget(self, request):
        self.checked.append(request)


def test_prefix_compaction_budget_repeated_prepare_and_cold_reconfiguration(tmp_path):
    async def scenario():
        repo = SQLiteSessionRepository(tmp_path / "history.db")
        thread, turn = new_thread_id(), new_turn_id()
        await repo.create_thread(thread, tmp_path)
        old = (
            UserMessageItem("old task " + "x" * 2400, turn),
            AssistantMessageItem("old answer " + "y" * 1200, turn, "s"),
        )
        await repo.append_items(thread, old)
        guide, model = prefix(), Model()
        window = ContextWindowManager(
            repository=repo,
            model=model,
            model_name="fixture",
            context_window_tokens=8000,
            auto_compact_tokens=700,
            request_context=(guide,),
        )
        snapshot = ContextSnapshot("base", (), tmp_path)
        try:
            prepared = await window.prepare(
                thread_id=thread, turn_id=turn, snapshot=snapshot, tools=()
            )
            assert prepared.compacted
            assert model.requests[0].context_items == (guide,)
            assert guide not in model.requests[0].items
            assert all(r.context_items == (guide,) for r in model.checked)
            assert not any(
                isinstance(i, ContextItem) and i.key == guide.key
                for i in await repo.load_items(thread)
            )
            for _ in range(3):
                before = len(await repo.load_items(thread))
                await window.prepare(thread_id=thread, turn_id=turn, snapshot=snapshot, tools=())
                assert len(await repo.load_items(thread)) == before
                assert window.request_context == (guide,)
            assert await window._estimate_request_tokens("base", (), ()) == estimate_request_tokens(
                "base", (guide,), ()
            )
            fresh = ContextWindowManager(
                repository=repo,
                model=model,
                model_name="fixture",
                context_window_tokens=8000,
                request_context=(prefix("New rule"),),
            )
            assert await fresh._estimate_request_tokens("base", (), ()) == estimate_request_tokens(
                "base", fresh.request_context, ()
            )
            await fresh._check_request_bytes("base", prepared.items)
            assert model.checked[-1].context_items[0].content == "New rule"
            clear = ContextWindowManager(
                repository=repo, model=model, model_name="fixture", context_window_tokens=8000
            )
            await clear._check_request_bytes("base", prepared.items)
            assert model.checked[-1].context_items == ()
            assert all(
                "Use business language" not in getattr(i, "content", "")
                for i in await repo.load_items(thread)
            )
        finally:
            await repo.close()

    asyncio.run(scenario())


def test_resume_does_not_trust_usage_from_a_different_request_prefix(tmp_path):
    async def scenario():
        model = Model()
        window = ContextWindowManager(
            repository=None,
            model=model,
            model_name="fixture",
            context_window_tokens=8000,
            request_context=(prefix(),),
        )
        item = UserMessageItem("task", "turn")
        old = ContextUsage(123, item.id, sample_id="old")
        assert await window._context_tokens_from_usage(old, (item,), (item,)) is None
        clone = window.with_model_settings(CorkiSettings(working_directory=tmp_path))
        assert clone.request_context == window.request_context
        assert await clone._context_tokens_from_usage(old, (item,), (item,)) is None
        assert (
            await clone._context_tokens_from_usage(replace(old, sample_id="new"), (item,), (item,))
            == 123
        )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "values", [(prefix(), prefix()), (replace(prefix(), role=ContextRole.USER),)]
)
def test_request_prefix_rejects_duplicate_keys_and_non_developer_messages(values):
    with pytest.raises(ValueError, match="request context"):
        ContextWindowManager(
            repository=None,
            model=Model(),
            model_name="fixture",
            context_window_tokens=8000,
            request_context=values,
        )


def test_oversized_prefix_is_counted_and_cannot_be_compacted_away(tmp_path):
    async def scenario():
        repo = SQLiteSessionRepository(tmp_path / "history.db")
        thread, turn = new_thread_id(), new_turn_id()
        await repo.create_thread(thread, tmp_path)
        model = Model()
        window = ContextWindowManager(
            repository=repo,
            model=model,
            model_name="fixture",
            context_window_tokens=500,
            request_context=(prefix("x" * 4000),),
        )
        try:
            with pytest.raises(ModelError, match="exceeds model window"):
                await window.prepare(
                    thread_id=thread,
                    turn_id=turn,
                    snapshot=ContextSnapshot("base", (), tmp_path),
                    tools=(),
                )
            assert not model.requests
            assert not await repo.load_items(thread)
        finally:
            await repo.close()

    asyncio.run(scenario())
