"""Local compaction keeps native-budget user evidence through real wire and cold replay."""

import asyncio
import json

import httpx
import pytest

from corki.config import CorkiSettings
from corki.context.history import active_history
from corki.context.tokens import estimate_request_tokens
from corki.core import LangGraphRuntime
from corki.models import (
    ModelCompleted,
    OpenAICompatibleModel,
    OpenAIResponsesModel,
    resolve_capabilities,
)
from corki.protocol.events import ContextCompacted, TurnCompleted
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.tools import ToolRegistry

COMPACT = "SUMMARIZE_FOR_USER_RETENTION"
CURRENT = "CURRENT_INPUT_MUST_REMAIN_VERBATIM"
OLDER = "OLDER_USER_OUTSIDE_THE_TEXT_BUDGET"


def source_case(kind):
    if kind == "ascii_boundary":
        text = "BEGIN_CONSTRAINT" + "x" * 79_969 + "TAIL_CONSTRAINT"
        assert len(text.encode()) == 80_000
        return text, text
    if kind == "unicode_boundary":
        text = "首" + "中" * 26_664 + "尾"
        assert len(text.encode()) == 79_998
        return text, text
    text = "BEGIN_CONSTRAINT" + "x" * 99_969 + "TAIL_CONSTRAINT"
    assert len(text.encode()) == 100_000
    # Pinned compact.rs's 20k text budget and utils/string truncation. The
    # marker is extra; do not derive this oracle from Corki's estimator/helper.
    return text, text[:40_000] + "…5000 tokens truncated…" + text[-40_000:]


def contents(item):
    content = item.get("content") or ""
    return content if isinstance(content, str) else "\n".join(p.get("text", "") for p in content)


def response(wire, *, call=False, high=False, summary=False):
    text = "RETAINED_CONTEXT_SUMMARY" if summary else "done"
    usage = 120_000 if high else 100
    if wire == "chat":
        delta = (
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "single-effect",
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
                "call_id": "single-effect",
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
                "id": "fixture-response",
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
@pytest.mark.parametrize("wire", ["chat", "responses"])
@pytest.mark.parametrize("kind", ["overlong", "ascii_boundary", "unicode_boundary"])
def test_local_user_budget_and_tail_survive_runtime_and_cold_replay(tmp_path, phase, wire, kind):
    async def scenario():
        original, expected = source_case(kind)
        bodies, compact_bodies, effects = [], [], []
        normal_count = 0

        class Effect:
            spec = ToolSpec("effect", "One observable fixture effect", {"type": "object"})

            async def execute(self, call, context):
                effects.append(call.id)
                return ToolResult(call.id, call.name, "ONE_OBSERVATION")

        def respond(request):
            nonlocal normal_count
            body = json.loads(request.content)
            bodies.append(body)
            messages = body.get("messages", body.get("input"))
            if contents(messages[-1]) == COMPACT:
                compact_bodies.append(body)
                return response(wire, summary=True)
            normal_count += 1
            return response(
                wire,
                call=normal_count == 3,
                high=(phase == "pre_turn" and normal_count == 2)
                or (phase == "mid_turn" and normal_count == 3),
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))

        async def create(thread=None):
            registry = ToolRegistry()
            registry.register(Effect())
            adapter = OpenAICompatibleModel if wire == "chat" else OpenAIResponsesModel
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    working_directory=tmp_path,
                    model="fixture",
                    api_mode="chat_completions" if wire == "chat" else "responses",
                    api_base="https://fixture.invalid/v1",
                    skills_enabled=False,
                    include_environment_context=False,
                    context_window_tokens=200_000,
                    auto_compact_tokens=100_000,
                    compact_prompt=COMPACT,
                    model_max_retries=0,
                ),
                model=adapter(
                    api_key="fixture",
                    base_url="https://fixture.invalid/v1",
                    client=client,
                    capabilities=resolve_capabilities(
                        base_url="https://fixture.invalid/v1",
                        api_mode="chat_completions" if wire == "chat" else "responses",
                    ),
                ),
                registry=registry,
                database_path=tmp_path / "s.db",
                home_path=tmp_path / ".corki",
                thread_id=thread,
            )

        runtime = await create()
        try:
            for text in (OLDER, original):
                events = [e async for e in runtime.stream(text)]
                assert isinstance(events[-1], TurnCompleted), events[-1]
            before = await runtime._repository.load_items(runtime.thread_id)
            source = next(
                i for i in before if isinstance(i, UserMessageItem) and i.content == original
            )
            if phase == "manual":
                events = [e async for e in runtime.compact()]
                assert isinstance(events[-1], TurnCompleted), events[-1]
                assert sum(isinstance(e, ContextCompacted) for e in events) == 1
            events = [e async for e in runtime.stream(CURRENT)]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert sum(isinstance(e, ContextCompacted) for e in events) == (phase != "manual")
            assert len(compact_bodies) == 1 and len(effects) == 1
            compact_messages = compact_bodies[0].get("messages", compact_bodies[0].get("input"))
            # Selection only changes the replacement, not what the summarizer saw.
            assert original in [contents(i) for i in compact_messages]
            assert (CURRENT in [contents(i) for i in compact_messages]) == (phase == "mid_turn")
            stored = await runtime._repository.load_items(runtime.thread_id)
            assert stored[: len(before)] == before
            visible = active_history(stored)
            retained = next(
                i
                for i in visible
                if isinstance(i, UserMessageItem) and i.retained_from_id == source.id
            )
            assert retained.content == expected
            assert retained.created_at == source.created_at and retained.turn_id == source.turn_id
            assert [i.content for i in visible if isinstance(i, UserMessageItem)] == [
                expected,
                CURRENT,
            ]
            assert sum(isinstance(i, CompactionItem) for i in visible) == 1
            assert sum(isinstance(i, ToolCallItem) for i in stored) == 1
            assert sum(isinstance(i, ToolResultItem) for i in stored) == 1
            thread = runtime.thread_id
            await runtime.aclose()
            runtime = await create(thread)
            events = [e async for e in runtime.stream("AFTER_REOPEN")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(compact_bodies) == 1 and len(effects) == 1
            cold = bodies[-1].get("messages", bodies[-1].get("input"))
            user_text = [contents(i) for i in cold if i.get("role") == "user"]
            assert expected in user_text and CURRENT in user_text and OLDER not in user_text
            assert (
                user_text.index(expected)
                < user_text.index(CURRENT)
                < user_text.index("AFTER_REOPEN")
            )
            assert (await runtime._repository.load_items(thread))[: len(stored)] == stored
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("unicode", [False, True])
def test_small_window_keeps_current_input_and_both_ends_of_older_evidence(tmp_path, unicode):
    old = "首" + "中" * 3998 + "尾" if unicode else "HEAD" + "x" * 23_992 + "TAIL"
    current = "当前" * 1000 if unicode else "CURRENT" * 1700

    async def scenario():
        normal, summaries, effects = [], [], []

        class Effect:
            spec = ToolSpec("effect", "One fixture effect", {"type": "object"})

            async def execute(self, call, context):
                effects.append(call.id)
                return ToolResult(call.id, call.name, "ONE_OBSERVATION")

        class Model:
            async def stream(self, request):
                turn, step = request.items[-1].turn_id, new_step_id()
                if (
                    isinstance(request.items[-1], UserMessageItem)
                    and request.items[-1].content == COMPACT
                ):
                    summaries.append(request)
                    yield ModelCompleted((AssistantMessageItem("SUMMARY", turn, step),))
                else:
                    normal.append(request)
                    item = (
                        ToolCallItem(ToolCall("effect-once", "effect", {}), turn, step)
                        if len(normal) == 2
                        else AssistantMessageItem("done", turn, step)
                    )
                    yield ModelCompleted((item,))

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Effect())
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                include_environment_context=False,
                # Native default permissions add real fixed-context cost; keep
                # the same two-compaction pressure with room for the first input.
                context_window_tokens=10000,
                auto_compact_tokens=9000,
                compact_prompt=COMPACT,
                model_max_retries=0,
            ),
            model=Model(),
            registry=registry,
            database_path=tmp_path / "s.db",
            home_path=tmp_path / ".corki",
        )
        try:
            assert isinstance([e async for e in runtime.stream(old)][-1], TurnCompleted)
            events = [e async for e in runtime.stream(current)]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert sum(isinstance(e, ContextCompacted) for e in events) == 2
            assert len(summaries) == 2 and len(effects) == 1 and len(normal) == 3
            for request in normal:
                assert request.instructions == normal[0].instructions
                assert (
                    estimate_request_tokens(request.instructions, request.items, request.tools)
                    < 9500
                )
            for request in normal[1:]:
                users = [i.content for i in request.items if isinstance(i, UserMessageItem)]
                assert users[-1] == current
                assert users[0].startswith("首" if unicode else "HEAD")
                assert users[0].endswith("尾" if unicode else "TAIL")
                assert "tokens truncated" in users[0]
            stored = await runtime._repository.load_items(runtime.thread_id)
            assert any(isinstance(i, UserMessageItem) and i.content == old for i in stored)
            assert sum(isinstance(i, ToolCallItem) for i in stored) == 1
            assert sum(isinstance(i, ToolResultItem) for i in stored) == 1
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
