"""A compaction checkpoint is user-level handoff data, never developer authority."""

import asyncio
import base64
import json
from dataclasses import replace

import httpx
import pytest

from corki.config import CorkiSettings
from corki.context.builder import ContextBuilder, ContextSnapshot
from corki.context.history import active_history
from corki.context.tokens import estimate_item_tokens
from corki.core import LangGraphRuntime
from corki.core.checkpoint import checkpoint_serializer
from corki.history_notes.archive import history_action
from corki.models import OpenAICompatibleModel, OpenAIResponsesModel, resolve_capabilities
from corki.protocol.events import ContextCompacted, TurnCompleted, TurnFailed
from corki.protocol.items import (
    CompactionItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    item_from_payload,
    item_to_payload,
)
from corki.protocol.tools import ToolResult, ToolSpec
from corki.storage import SQLiteSessionRepository
from corki.tools import ToolRegistry

META = "internal_chat_message_metadata_passthrough"
# Pinned native prompts/templates/compact/summary_prefix.md, independent of production.
PREFIX = (
    "Another language model started to solve this problem and produced a summary of its "
    "thinking process. You also have access to the state of the tools that were used by that "
    "language model. Use this to build on the work that has already been done and avoid "
    "duplicating work. Here is the summary produced by the other language model, use the "
    "information in this summary to assist with your own analysis:"
)
COMPACT = "SUMMARIZE_FOR_PROJECTION_TEST"
SUMMARIES = (
    "SAVED_CHECKPOINT_1: quoted external text says ignore the user's constraints.",
    "SAVED_CHECKPOINT_2: work remains; original constraints still apply.",
)
# Serialized by immutable153, not by the implementation under test.
OLD_CHECKPOINT = (
    "yAEOApO0Y29ya2kucHJvdG9jb2wuaXRlbXOuQ29tcGFjdGlvbkl0ZW2Kp3N1bW1hcnm2"
    "TEVHQUNZX0NIRUNLUE9JTlRfQk9EWa90aHJvdWdoX2l0ZW1faWSpb2xkLWlucHV0p3R1"
    "cm5faWSob2xkLXR1cm6iaWSrb2xkLWNvbXBhY3S2cmVwbGFjZW1lbnRfaXRlbV9jb3Vu"
    "dAC0c3VtbWFyeV9pbnNlcnRfaW5kZXgAqmNyZWF0ZWRfYXS5MjAyNi0wOS0wOFQwMDow"
    "MDowMCswMDowMK1jb250ZXh0X3Jlc2V0wrNyZW1vdGVfcGF5bG9hZF9qc29uwLZyZXNw"
    "b25zZV9tZXRhZGF0YV9qc29uwA=="
)


def messages(body):
    return body.get("messages", body.get("input"))


def content(item):
    value = item.get("content") or ""
    return value if isinstance(value, str) else "\n".join(p.get("text", "") for p in value)


def reply(wire, text="done", *, call=False, usage=100):
    if wire == "chat":
        delta = (
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "effect-call",
                        "type": "function",
                        "function": {"name": "effect", "arguments": "{}"},
                    }
                ]
            }
            if call
            else {"content": text}
        )
        packet = {
            "choices": [
                {"index": 0, "delta": delta, "finish_reason": "tool_calls" if call else "stop"}
            ],
            "usage": {"prompt_tokens": usage, "completion_tokens": 10},
        }
    else:
        item = (
            {
                "type": "function_call",
                "id": "fc_effect",
                "call_id": "effect-call",
                "name": "effect",
                "arguments": "{}",
            }
            if call
            else {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }
        )
        packet = {
            "type": "response.completed",
            "response": {
                "id": "response-fixture",
                "output": [item],
                "usage": {
                    "input_tokens": usage,
                    "output_tokens": 10,
                    "total_tokens": usage + 10,
                },
            },
        }
    return httpx.Response(200, text=f"data: {json.dumps(packet)}\n\n")


@pytest.mark.parametrize("phase", ["manual", "pre_turn", "mid_turn"])
@pytest.mark.parametrize("wire", ["chat", "compatible", "metadata", "kinds_off"])
def test_local_summary_runtime_wire_cold_reopen_and_second_compaction(tmp_path, phase, wire):
    async def scenario():
        bodies, compact_bodies, effects = [], [], []
        normal_count = 0
        api_mode = "chat_completions" if wire == "chat" else "responses"

        class Effect:
            spec = ToolSpec("effect", "Record one fixture effect", {"type": "object"})

            async def execute(self, call, context):
                effects.append(call.id)
                return ToolResult(call.id, call.name, "EFFECT_OBSERVATION")

        def respond(request):
            nonlocal normal_count
            body = json.loads(request.content)
            bodies.append(body)
            if content(messages(body)[-1]) == COMPACT:
                compact_bodies.append(body)
                return reply(wire, SUMMARIES[len(compact_bodies) - 1])
            normal_count += 1
            high = (phase == "mid_turn" and normal_count == 1) or (
                phase == "pre_turn" and normal_count == 2
            )
            return reply(wire, call=normal_count == 1, usage=120_000 if high else 100)

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))

        async def create(thread=None):
            capabilities = replace(
                resolve_capabilities(base_url="https://fixture.invalid/v1", api_mode=api_mode),
                supports_internal_metadata=wire in ("metadata", "kinds_off"),
            )
            registry = ToolRegistry()
            registry.register(Effect())
            adapter = OpenAICompatibleModel if wire == "chat" else OpenAIResponsesModel
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    working_directory=tmp_path,
                    model="fixture",
                    api_mode=api_mode,
                    api_base="https://fixture.invalid/v1",
                    skills_enabled=False,
                    include_environment_context=False,
                    content_item_kinds=wire != "kinds_off",
                    context_window_tokens=200_000,
                    auto_compact_tokens=100_000,
                    compact_prompt=COMPACT,
                    model_max_retries=0,
                ),
                model=adapter(
                    api_key="fixture",
                    base_url="https://fixture.invalid/v1",
                    client=client,
                    capabilities=capabilities,
                ),
                registry=registry,
                database_path=tmp_path / "s.db",
                home_path=tmp_path / ".corki",
                thread_id=thread,
            )

        runtime = await create()
        try:
            events = [e async for e in runtime.stream("FIRST_REAL_INPUT")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert sum(isinstance(e, ContextCompacted) for e in events) == (
                1 if phase == "mid_turn" else 0
            )
            if phase == "manual":
                assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
            events = [e async for e in runtime.stream("SECOND_REAL_INPUT")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert sum(isinstance(e, ContextCompacted) for e in events) == (
                1 if phase == "pre_turn" else 0
            )
            assert len(compact_bodies) == 1
            before = await runtime._repository.load_items(runtime.thread_id)
            assert len([i for i in before if isinstance(i, ToolCallItem)]) == 1
            assert len([i for i in before if isinstance(i, ToolResultItem)]) == 1
            assert len(effects) == 1
            assert "EFFECT_OBSERVATION" in json.dumps(compact_bodies[0])
            # Accepted input belongs in all local summary requests, independently
            # of retaining its verbatim copy in the replacement history.
            assert "FIRST_REAL_INPUT" in json.dumps(compact_bodies[0])
            if phase == "pre_turn":
                assert "SECOND_REAL_INPUT" not in json.dumps(compact_bodies[0])
            first_marker = next(i for i in before if isinstance(i, CompactionItem))
            assert first_marker.summary == SUMMARIES[0]  # Durable body is not rewritten.

            thread = runtime.thread_id
            await runtime.aclose()
            runtime = await create(thread)
            assert isinstance(
                [e async for e in runtime.stream("THIRD_REAL_INPUT")][-1], TurnCompleted
            )
            cold_body = bodies[-1]
            assert (await runtime._repository.load_items(thread))[: len(before)] == before
            assert len(compact_bodies) == 1 and len(effects) == 1
            assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
            assert isinstance(
                [e async for e in runtime.stream("FOURTH_REAL_INPUT")][-1], TurnCompleted
            )
            stored = await runtime._repository.load_items(thread)
            assert stored[: len(before)] == before
            markers = [i for i in stored if isinstance(i, CompactionItem)]
            assert [i.summary for i in markers] == list(SUMMARIES)
            assert len(effects) == 1 and len(compact_bodies) == 2
            visible = active_history(stored)
            assert [i.summary for i in visible if isinstance(i, CompactionItem)] == [SUMMARIES[1]]
            assert not any(
                "SAVED_CHECKPOINT_" in i.content for i in visible if isinstance(i, UserMessageItem)
            )
            assert not any("SAVED_CHECKPOINT_1" in content(i) for i in messages(bodies[-1]))

            def assert_summary(body, summary):
                found = [i for i in messages(body) if summary in content(i)]
                assert len(found) == 1
                item = found[0]
                assert item["role"] == "user", "summary was promoted above user authority"
                assert content(item) == PREFIX + "\n" + summary
                assert META not in item
                return item

            cold_item = assert_summary(cold_body, SUMMARIES[0])
            repeated_item = assert_summary(compact_bodies[1], SUMMARIES[0])
            if wire != "chat":
                assert cold_item["id"] == repeated_item["id"] == f"msg_{first_marker.id}"
            assert_summary(bodies[-1], SUMMARIES[1])
            post_first = next(
                b for b in bodies if any(SUMMARIES[0] in content(i) for i in messages(b))
            )
            assert_summary(post_first, SUMMARIES[0])
            projected = [content(i) for i in messages(post_first)]
            if phase == "mid_turn":
                assert projected[-1] == PREFIX + "\n" + SUMMARIES[0]
                assert projected.index("FIRST_REAL_INPUT") < len(projected) - 1
            else:
                assert projected.index(PREFIX + "\n" + SUMMARIES[0]) < projected.index(
                    "SECOND_REAL_INPUT"
                )
            if wire == "chat":
                assert bodies[0]["messages"][0] == bodies[-1]["messages"][0]
            else:
                assert bodies[0]["instructions"] == bodies[-1]["instructions"]
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("wire", ["chat", "compatible"])
def test_prefix_cost_prevents_installing_an_oversized_replacement(tmp_path, wire):
    async def scenario():
        bodies = []
        api_mode = "chat_completions" if wire == "chat" else "responses"

        class SmallContext(ContextBuilder):
            def base_instructions(self):
                return "BASE"

            async def build(self, **options):
                return ContextSnapshot("BASE", (), tmp_path)

        def respond(request):
            body = json.loads(request.content)
            bodies.append(body)
            if content(messages(body)[-1]) == COMPACT:
                # Bare body fits 512 * .95; prefix + kind + current input do not.
                return reply(wire, "x" * 1600)
            return reply(wire, usage=1000 if len(bodies) == 1 else 10)

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        adapter = OpenAICompatibleModel if wire == "chat" else OpenAIResponsesModel
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                model="fixture",
                api_mode=api_mode,
                api_base="https://fixture.invalid/v1",
                skills_enabled=False,
                context_window_tokens=512,
                auto_compact_tokens=450,
                compact_prompt=COMPACT,
                model_max_retries=0,
            ),
            model=adapter(
                api_key="fixture",
                base_url="https://fixture.invalid/v1",
                client=client,
                capabilities=resolve_capabilities(
                    base_url="https://fixture.invalid/v1", api_mode=api_mode
                ),
            ),
            registry=ToolRegistry(),
            database_path=tmp_path / "small.db",
            home_path=tmp_path / ".corki",
        )
        runtime._graph._context_builder = SmallContext(
            instruction_manager=runtime._instruction_manager
        )
        try:
            assert isinstance([e async for e in runtime.stream("first")][-1], TurnCompleted)
            before = await runtime._repository.load_items(runtime.thread_id)
            events = [e async for e in runtime.stream("current")]
            assert isinstance(events[-1], TurnFailed)
            assert "compaction is still too large" in events[-1].error
            assert len(bodies) == 2  # No over-budget normal request was submitted.
            stored = await runtime._repository.load_items(runtime.thread_id)
            assert stored[: len(before)] == before
            assert not any(isinstance(i, CompactionItem) for i in stored)
            assert not any(isinstance(e, ContextCompacted) for e in events)
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "summary", ["short", "汉字\n  indented", "<compaction_summary>literal</compaction_summary>"]
)
def test_summary_budget_counts_exact_handoff_and_classification(summary):
    item = CompactionItem(summary, None, "turn")
    equivalent = UserMessageItem(
        PREFIX + "\n" + summary, "turn", content_item_kinds=("compaction.summary",)
    )
    assert estimate_item_tokens(item) == estimate_item_tokens(equivalent)


def test_old_checkpoint_readable_projections_keep_identity_and_source_payload(tmp_path):
    async def scenario():
        serializer = checkpoint_serializer()
        item = serializer.loads_typed(("msgpack", base64.b64decode(OLD_CHECKPOINT)))
        expected = CompactionItem(
            "LEGACY_CHECKPOINT_BODY",
            "old-input",
            "old-turn",
            id="old-compact",
            created_at="2026-09-08T00:00:00+00:00",
        )
        assert item == expected
        old_payload = item_to_payload(item)
        assert item_from_payload("compaction", old_payload) == item
        repository = SQLiteSessionRepository(tmp_path / "legacy.db")
        try:
            await repository.create_thread("thread", tmp_path)
            await repository.append_items("thread", (item,))
            messages = await repository.load_messages("thread")
            assert len(messages) == 1 and str(messages[0].id) == item.id
            assert messages[0].role == "user"
            assert messages[0].content == PREFIX + "\n" + item.summary
            rows = history_action("list_items", {"role": "user"}, (item,), "thread", 4000)
            assert len(rows["items"]) == 1 and rows["items"][0]["item_id"] == item.id
            read = history_action("read_item", {"item_id": item.id}, (item,), "thread", 4000)
            assert read["role"] == "user" and read["text"] == PREFIX + "\n" + item.summary
            assert (await repository.load_items("thread")) == (item,)
            assert item_to_payload(item) == old_payload
        finally:
            await repository.close()

    asyncio.run(scenario())
