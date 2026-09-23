"""Citation visibility, raw replay and usage through actual HTTP-backed Runtime."""

import asyncio
import json

import httpx
import pytest

from corki.config import CorkiSettings
from corki.config.model_context import parse_model_contexts
from corki.core import LangGraphRuntime
from corki.core.checkpoint import checkpoint_serializer
from corki.memory.sqlite import SQLiteMemoryRepository
from corki.models import (
    ModelCompleted,
    OpenAICompatibleModel,
    OpenAIResponsesModel,
    resolve_capabilities,
)
from corki.protocol.events import AssistantMessageCompleted, AssistantTextDelta, TurnCompleted
from corki.protocol.ids import new_thread_id, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    UserMessageItem,
    item_from_payload,
    item_to_payload,
    new_step_id,
)
from corki.protocol.memory import MemoryCitation
from corki.sessions import TurnRecord, TurnStatus
from corki.storage import SQLiteSessionRepository

SOURCE = "019cc2ea-1dff-7902-8d40-c8f6e5d83cc4"
OTHER = "019cc2ea-1dff-7902-8d40-c8f6e5d83cc5"


def citation(tag="oai-mem-citation"):
    return (
        f"<{tag}><rollout_ids>{SOURCE}\ninvalid\n{SOURCE}</rollout_ids>"
        f"<thread_ids>{OTHER}</thread_ids></{tag}>"
    )


def message(identity, text):
    return {
        "type": "message",
        "id": identity,
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
    }


@pytest.mark.parametrize("mode", ["chat", "responses", "lite"])
@pytest.mark.parametrize("streamed", [True, False])
@pytest.mark.parametrize("case", ["valid", "invalid", "unterminated", "multiple"])
def test_citation_runtime_visibility_usage_and_cold_raw_replay(tmp_path, mode, streamed, case):
    async def scenario():
        bodies, usage = [], []
        suffix_seen = asyncio.Event()
        raw = {
            "valid": "before\n" + citation() + "suffix",
            "invalid": "before\n<oai-mem-citation>invalid</oai-mem-citation>suffix",
            "unterminated": "before\n<oai-mem-citation>invalid",
            "multiple": "before\n" + citation() + citation("corki-memory-citation") + "suffix",
        }[case]
        visible = "before\n" + ("" if case == "unterminated" else "suffix")

        class Memories(SQLiteMemoryRepository):
            async def mark_memories_used(self, ids):
                usage.extend(ids)
                await super().mark_memories_used(ids)

        class Packets(httpx.AsyncByteStream):
            async def __aiter__(self):
                def packet(value):
                    return ("data: " + json.dumps(value) + "\n\n").encode()

                if streamed:
                    for char in raw:
                        yield packet(
                            {"choices": [{"index": 0, "delta": {"content": char}}]}
                            if mode == "chat"
                            else {
                                "type": "response.output_text.delta",
                                "item_id": "first",
                                "delta": char,
                            }
                        )
                    if case != "unterminated":
                        await asyncio.wait_for(suffix_seen.wait(), 2)
                if mode == "chat":
                    yield packet(
                        {
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {} if streamed else {"content": raw},
                                    "finish_reason": "stop",
                                }
                            ]
                        }
                    )
                    yield b"data: [DONE]\n\n"
                else:
                    output = [message("first", raw)]
                    if streamed:
                        yield packet({"type": "response.output_item.done", "item": output[0]})
                    yield packet(
                        {"type": "response.completed", "response": {"id": "r", "output": output}}
                    )

        def handle(request):
            bodies.append(json.loads(request.content))
            assert request.headers.get("x-openai-internal-codex-responses-lite") is None
            if len(bodies) == 1:
                return httpx.Response(200, stream=Packets())
            if mode == "chat":
                value = {
                    "choices": [{"index": 0, "delta": {"content": "done"}, "finish_reason": "stop"}]
                }
                return httpx.Response(
                    200, text="data: " + json.dumps(value) + "\n\ndata: [DONE]\n\n"
                )
            value = {
                "type": "response.completed",
                "response": {"id": "next", "output": [message("next", "done")]},
            }
            return httpx.Response(200, text="data: " + json.dumps(value) + "\n\n")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        database = tmp_path / "sessions.db"

        async def runtime(thread=None):
            adapter = OpenAICompatibleModel if mode == "chat" else OpenAIResponsesModel
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    working_directory=tmp_path,
                    skills_enabled=False,
                    model_max_retries=0,
                    model="citation-model",
                    model_contexts=parse_model_contexts(
                        {
                            "citation-model": {
                                "context_window": 272000,
                                "use_responses_lite": mode == "lite",
                            }
                        }
                    ),
                ),
                database_path=database,
                repository=SQLiteSessionRepository(database),
                memory_repository=Memories(database),
                thread_id=thread,
                model=adapter(
                    api_key="fixture",
                    base_url="https://fixture.invalid/v1",
                    client=client,
                    capabilities=resolve_capabilities(
                        base_url="https://fixture.invalid/v1",
                        api_mode="chat_completions" if mode == "chat" else "responses",
                        provider_name="openai",
                    ),
                ),
            )

        first = await runtime()
        try:
            events, deltas = [], ""
            async for event in first.stream("answer"):
                events.append(event)
                if isinstance(event, AssistantTextDelta):
                    deltas += event.delta
                    if deltas.endswith("suffix"):
                        suffix_seen.set()
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert deltas == visible
            assert events[-1].final_answer == visible
            assert [e.text for e in events if isinstance(e, AssistantMessageCompleted)] == [visible]
            saved = next(
                i
                for i in await first._repository.load_items(first.thread_id)
                if isinstance(i, AssistantMessageItem)
            )
            assert saved.content == visible
            assert json.loads(saved.response_body_json)["content"] == [
                {"type": "output_text", "text": raw}
            ]
            if case in {"valid", "multiple"}:
                assert saved.memory_citation.rollout_ids == (SOURCE, "invalid")
                assert saved.memory_citation.thread_ids == (SOURCE,)
                assert usage == [SOURCE]
            else:
                assert saved.memory_citation is None and usage == []
            # Canonical JSON/checkpoint reconstruction keeps both display and raw body.
            assert item_from_payload("assistant_message", item_to_payload(saved)) == saved
            codec = checkpoint_serializer()
            assert codec.loads_typed(codec.dumps_typed(saved)) == saved
            thread = first.thread_id
        finally:
            await first.aclose()
        cold = await runtime(thread)
        try:
            events = [e async for e in cold.stream("continue")]
            assert isinstance(events[-1], TurnCompleted)
            body = bodies[-1]
            assistant = next(
                i
                for i in body["messages" if mode == "chat" else "input"]
                if i.get("role") == "assistant"
            )
            if mode == "chat":
                assert assistant["content"] == raw
            else:
                assert assistant["content"] == [{"type": "output_text", "text": raw}]
            assert saved in await cold._repository.load_items(thread)
            assert usage == ([SOURCE] if case in {"valid", "multiple"} else [])
        finally:
            await cold.aclose()
            await client.aclose()

    asyncio.run(scenario())


def test_cold_resume_of_committed_step_does_not_count_memory_usage_again(tmp_path):
    async def scenario():
        database = tmp_path / "s.db"
        repository = SQLiteSessionRepository(database)
        thread, turn = new_thread_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        user = UserMessageItem("recover", turn)
        await repository.save_turn(TurnRecord(turn, thread, TurnStatus.RUNNING, user.content))
        await repository.append_items(thread, (user,))
        answer = AssistantMessageItem(
            "answer", turn, new_step_id(), memory_citation=MemoryCitation(thread_ids=(SOURCE,))
        )
        await repository.commit_model_step(thread, turn, 0, ModelCompleted((answer,)))
        usage = []

        class Memories(SQLiteMemoryRepository):
            async def mark_memories_used(self, ids):
                usage.extend(ids)

        class Model:
            async def stream(self, request):
                raise AssertionError("a committed model step must not be resampled")
                yield

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(tmp_path, skills_enabled=False),
            database_path=database,
            repository=repository,
            thread_id=thread,
            memory_repository=Memories(database),
            model=Model(),
        )
        try:
            events = [e async for e in runtime.resume_pending()]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert events[-1].final_answer == "answer"
            assert usage == []
            assert [
                i
                for i in await repository.load_items(thread)
                if isinstance(i, AssistantMessageItem)
            ] == [answer]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("done_count", [0, 1, 2])
def test_each_completed_message_records_usage_once_even_when_last_is_citation_only(
    tmp_path, done_count
):
    async def scenario():
        usage = []

        class Memories(SQLiteMemoryRepository):
            async def mark_memories_used(self, ids):
                usage.extend(ids)
                await super().mark_memories_used(ids)

        output = [message("first", "answer" + citation()), message("last", citation())]

        def handle(request):
            events = [
                {"type": "response.output_item.done", "item": item} for item in output[:done_count]
            ]
            events.append({"type": "response.completed", "response": {"id": "r", "output": output}})
            return httpx.Response(
                200, text="".join("data: " + json.dumps(e) + "\n\n" for e in events)
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        database = tmp_path / "s.db"
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(tmp_path, skills_enabled=False, model_max_retries=0),
            database_path=database,
            repository=SQLiteSessionRepository(database),
            memory_repository=Memories(database),
            model=OpenAIResponsesModel(
                api_key="fixture",
                base_url="https://fixture.invalid/v1",
                client=client,
                capabilities=resolve_capabilities(
                    base_url="https://fixture.invalid/v1", api_mode="responses"
                ),
            ),
        )
        try:
            events = [e async for e in runtime.stream("answer")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert usage == [SOURCE, SOURCE]
            assert events[-1].final_answer == "answer"
            assert [e.text for e in events if isinstance(e, AssistantMessageCompleted)] == [
                "answer"
            ]
            answers = [
                i
                for i in await runtime._repository.load_items(runtime.thread_id)
                if isinstance(i, AssistantMessageItem)
            ]
            assert [a.content for a in answers] == ["answer", ""]
            assert all(a.memory_citation.rollout_ids == (SOURCE, "invalid") for a in answers)
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())
