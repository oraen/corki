"""Mixed complete output items must retain order, identity and opaque state."""

import asyncio
import json

import httpx
import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.memory.sqlite import SQLiteMemoryRepository
from corki.models import (
    ModelCompleted,
    ModelItemCompleted,
    OpenAIResponsesModel,
    resolve_capabilities,
)
from corki.protocol.events import (
    AssistantMessageCompleted,
    AssistantTextDelta,
    TurnCompleted,
    TurnFailed,
)
from corki.protocol.items import (
    AssistantMessageItem,
    ReasoningItem,
    ToolCallItem,
    ToolResultItem,
    new_step_id,
)
from corki.protocol.tools import ToolResult, ToolSpec
from corki.storage import SQLiteSessionRepository
from corki.tools import ToolRegistry


def message(identity, content, phase="commentary"):
    return {
        "type": "message",
        "id": identity,
        "phase": phase,
        "content": [{"type": "output_text", "text": content}],
    }


@pytest.mark.parametrize("truncate", [False, True])
def test_mixed_done_items_survive_stream_and_replay_independently(tmp_path, truncate):
    async def scenario():
        citation = (
            "<corki-memory-citation><citation_entries>MEMORY.md:1-2|note=[fixture]</citation_entries>"
            "<thread_ids>00000000-0000-0000-0000-000000000001</thread_ids></corki-memory-citation>"
        )
        items = [
            message("m1", "first" + citation),
            {"type": "reasoning", "id": "r1", "summary": [], "encrypted_content": "opaque-one"},
            {
                "type": "function_call",
                "id": "tool",
                "call_id": "c1",
                "name": "probe",
                "arguments": "{}",
            },
            message("m2", "second"),
            {
                "type": "reasoning",
                "id": "r2",
                "summary": [{"type": "summary_text", "text": "summary two"}],
                "encrypted_content": "opaque-two",
            },
        ]
        packets = []
        for item in items:
            if item["type"] == "message":
                packets.append(
                    {
                        "type": "response.output_text.delta",
                        "item_id": item["id"],
                        "delta": item["content"][0]["text"],
                    }
                )
            packets.append({"type": "response.output_item.done", "item": item})
        if not truncate:
            packets.append(
                {"type": "response.completed", "response": {"id": "response1", "output": items}}
            )
        requests = []
        repository = SQLiteSessionRepository(tmp_path / "sessions.db")
        usage_calls = []

        class Memories(SQLiteMemoryRepository):
            async def mark_memories_used(self, thread_ids):
                durable = await repository.load_items(runtime.thread_id)
                assert any(
                    isinstance(item, AssistantMessageItem) and item.memory_citation is not None
                    for item in durable
                )
                usage_calls.append(tuple(thread_ids))
                await super().mark_memories_used(thread_ids)

        class Tool:
            spec = ToolSpec("probe", "fixture", {"type": "object"})

            async def execute(self, call, context):
                return ToolResult(call.id, call.name, "observed")

        async def handle(request):
            requests.append(json.loads(request.content))
            values = (
                packets
                if len(requests) == 1
                else [
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "response2",
                            "output": [
                                message("checking", "checking"),
                                message("final", "done", "final_answer"),
                            ],
                        },
                    }
                ]
            )
            return httpx.Response(
                200, text="".join(f"data: {json.dumps(value)}\n\n" for value in values)
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        model = OpenAIResponsesModel(
            api_key="fixture",
            base_url="https://fixture.invalid/v1",
            client=client,
            capabilities=resolve_capabilities(
                base_url="https://fixture.invalid/v1", api_mode="responses"
            ),
        )
        registry = ToolRegistry()
        registry.register(Tool())
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, model_max_retries=0
            ),
            database_path=repository.path,
            repository=repository,
            memory_repository=Memories(repository.path),
            registry=registry,
            model=model,
        )
        try:
            events = [event async for event in runtime.stream("run")]
            assert isinstance(events[-1], TurnFailed if truncate else TurnCompleted), events[-1]
            history = [
                item
                for item in await repository.load_items(runtime.thread_id)
                if isinstance(
                    item, (AssistantMessageItem, ReasoningItem, ToolCallItem, ToolResultItem)
                )
            ]
            assert [type(item) for item in history[:6]] == [
                AssistantMessageItem,
                ReasoningItem,
                ToolCallItem,
                AssistantMessageItem,
                ReasoningItem,
                ToolResultItem,
            ]
            assert history[0].content == "first" and history[0].memory_citation is not None
            assert usage_calls == [("00000000-0000-0000-0000-000000000001",)]
            assert history[3].content == "second"
            assert [
                (item.content, item.encrypted_content)
                for item in history
                if isinstance(item, ReasoningItem)
            ] == [("", "opaque-one"), ("summary two", "opaque-two")]
            visible = "".join(
                event.delta for event in events if isinstance(event, AssistantTextDelta)
            )
            assert visible == ("firstsecond" if truncate else "firstsecondcheckingdone")
            messages = [
                event.text for event in events if isinstance(event, AssistantMessageCompleted)
            ]
            assert messages == (
                ["first", "second"] if truncate else ["first", "second", "checking", "done"]
            )
            if not truncate:
                assert events[-1].final_answer == "done"
                assert history[0].phase == "commentary" and history[-1].phase == "final_answer"
                replayed_messages = [
                    item for item in requests[1]["input"] if item.get("role") == "assistant"
                ]
                assert [item["phase"] for item in replayed_messages] == ["commentary", "commentary"]
                replayed = [
                    item for item in requests[1]["input"] if item.get("type") == "reasoning"
                ]
                assert [item["encrypted_content"] for item in replayed] == [
                    "opaque-one",
                    "opaque-two",
                ]
                assert [item["id"] for item in replayed] == ["r1", "r2"]
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


def test_steering_after_completed_message_without_tools_is_a_continuation(tmp_path):
    async def scenario():
        waiting = asyncio.Event()
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    yield ModelItemCompleted(AssistantMessageItem("first message", turn, step))
                    waiting.set()
                    await asyncio.Event().wait()
                else:
                    assert any(
                        isinstance(item, AssistantMessageItem) and item.content == "first message"
                        for item in request.items
                    )
                    assert request.items[-1].content == "new input"
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "sessions.db",
            model=Model(),
        )

        async def collect():
            return [event async for event in runtime.stream("run", realtime=True)]

        task = asyncio.create_task(collect())
        try:
            await asyncio.wait_for(waiting.wait(), 2)
            await runtime.steer("new input")
            events = await asyncio.wait_for(task, 2)
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert events[-1].final_answer == "done" and len(requests) == 2
            assert [
                event.text for event in events if isinstance(event, AssistantMessageCompleted)
            ] == ["first message", "done"]
        finally:
            await runtime.aclose()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
