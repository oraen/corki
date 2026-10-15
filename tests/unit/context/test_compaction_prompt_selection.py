"""Custom local compaction instructions preserve Codex Option<String> semantics."""

import asyncio

import pytest

from corki.context import ContextWindowManager
from corki.models import ModelCompleted
from corki.prompting import PromptStore
from corki.protocol.ids import new_turn_id
from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id
from corki.storage import SQLiteSessionRepository


@pytest.mark.parametrize("custom", [None, "", " \n\t", "  Keep exact details.\n\n"])
def test_compaction_prompt_reaches_request_without_stripping_or_falsey_fallback(tmp_path, custom):
    async def scenario():
        turn = new_turn_id()
        history = (UserMessageItem("original request", turn),)
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted((AssistantMessageItem("handoff", turn, new_step_id()),))

        repository = SQLiteSessionRepository(tmp_path / "sessions.db")
        manager = ContextWindowManager(
            repository=repository,
            model=Model(),
            model_name="fixture",
            context_window_tokens=8000,
            compact_prompt=custom,
        )
        try:
            assert (
                await manager._summarize(history, instructions="original base", turn_id=turn)
                == "handoff"
            )
            (request,) = requests
            assert request.instructions == "original base" and request.tools == ()
            assert request.items[:-1] == history
            assert isinstance(request.items[-1], UserMessageItem)
            assert request.items[-1].content == (
                PromptStore().render("tasks/compact") if custom is None else custom
            )
        finally:
            await repository.close()

    asyncio.run(scenario())
