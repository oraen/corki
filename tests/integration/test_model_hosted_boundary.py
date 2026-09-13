"""New model-port hosted events are rejected; old committed records remain readable."""

import asyncio
import json

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.memory.sqlite import SQLiteMemoryRepository
from corki.models import ModelCompleted, ModelItemCompleted
from corki.protocol.events import TurnCompleted, TurnFailed
from corki.protocol.ids import new_thread_id, new_turn_id
from corki.protocol.items import AssistantMessageItem, HostedToolItem, UserMessageItem, new_step_id
from corki.protocol.memory import MemoryCitation
from corki.sessions import TurnRecord, TurnStatus
from corki.storage import SQLiteSessionRepository
from corki.tools import ToolRegistry


def hosted(turn, step):
    return HostedToolItem(
        json.dumps({"type": "web_search_call", "id": "legacy", "status": "completed"}),
        turn,
        step,
    )


@pytest.mark.parametrize("kind", ["hosted", "wrong_turn", "duplicate", "valid"])
def test_model_contract_is_checked_before_memory_usage_feedback(tmp_path, kind):
    async def scenario():
        used, requests = [], []
        source = new_thread_id()

        class Memories(SQLiteMemoryRepository):
            async def mark_memories_used(self, ids):
                used.extend(ids)
                await super().mark_memories_used(ids)

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                answer = AssistantMessageItem(
                    "answer",
                    new_turn_id() if kind == "wrong_turn" else turn,
                    step,
                    memory_citation=MemoryCitation(thread_ids=(source,)),
                )
                items = (
                    (hosted(turn, step), answer)
                    if kind == "hosted"
                    else ((answer, answer) if kind == "duplicate" else (answer,))
                )
                yield ModelCompleted(items)

            async def aclose(self):
                pass

        database = tmp_path / "usage.db"
        repository = SQLiteSessionRepository(database)
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(tmp_path, skills_enabled=False),
            model=Model(),
            registry=ToolRegistry(),
            database_path=database,
            repository=repository,
            memory_repository=Memories(database),
        )
        try:
            events = [event async for event in runtime.stream("check result")]
            terminal = [event for event in events if isinstance(event, (TurnCompleted, TurnFailed))]
            assert len(terminal) == len(requests) == 1
            if kind == "valid":
                assert isinstance(terminal[0], TurnCompleted)
                assert used == [source]
            else:
                assert isinstance(terminal[0], TurnFailed)
                assert terminal[0].error_kind == "protocol"
                assert used == []
                assert (
                    await runtime._repository.load_model_step(
                        runtime.thread_id, terminal[0].turn_id, 0
                    )
                    is None
                )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("streamed", [False, True])
def test_new_hosted_items_from_custom_model_cannot_commit_or_finish(tmp_path, streamed):
    async def scenario():
        calls = []

        class Model:
            async def stream(self, request):
                calls.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                item = hosted(turn, step)
                if streamed:
                    yield ModelItemCompleted(item)
                yield ModelCompleted((item, AssistantMessageItem("done", turn, step)))

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(tmp_path, skills_enabled=False),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "s.db",
        )
        try:
            events = [event async for event in runtime.stream("ordinary only")]
            terminal = [event for event in events if isinstance(event, (TurnCompleted, TurnFailed))]
            assert len(terminal) == 1 and isinstance(terminal[0], TurnFailed)
            assert terminal[0].error_kind == "protocol" and not terminal[0].retryable
            assert len(calls) == 1
            items = await runtime._repository.load_items(runtime.thread_id)
            assert not any(isinstance(item, HostedToolItem) for item in items)
            assert (
                await runtime._repository.load_model_step(runtime.thread_id, terminal[0].turn_id, 0)
                is None
            )
            assert (
                await runtime._repository.load_partial_step(
                    runtime.thread_id, terminal[0].turn_id, 0
                )
                == ()
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("committed", [False, True])
def test_legacy_hosted_step_recovers_without_replaying_saved_work(tmp_path, committed):
    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "s.db")
        thread, turn, step = new_thread_id(), new_turn_id(), new_step_id()
        await repository.create_thread(thread, tmp_path)
        await repository.save_turn(TurnRecord(turn, thread, TurnStatus.RUNNING, "old task"))
        await repository.append_items(thread, (UserMessageItem("old task", turn),))
        completed = ModelCompleted((hosted(turn, step), AssistantMessageItem("saved", turn, step)))
        if committed:
            await repository.commit_model_step(thread, turn, 0, completed)
        else:
            await repository.append_partial_item(thread, turn, 0, completed.items[0])
        original = await repository.load_items(thread)
        requests = []

        class Model:
            async def stream(self, request):
                assert not committed, "committed legacy step must not be sampled again"
                requests.append(request)
                yield ModelCompleted((AssistantMessageItem("saved", turn, new_step_id()),))

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(tmp_path, skills_enabled=False),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "s.db",
            repository=repository,
            thread_id=thread,
        )
        try:
            events = [event async for event in runtime.resume_pending()]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert events[-1].final_answer == "saved"
            assert (await repository.load_items(thread))[: len(original)] == original
            assert await repository.load_model_step(thread, turn, 0) == (
                completed if committed else None
            )
            assert len(requests) == (0 if committed else 1)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
