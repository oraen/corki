import asyncio
import json

import httpx
import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.models import ModelCompleted, ModelError
from corki.protocol.events import ContextCompacted, TurnCompleted, TurnFailed
from corki.protocol.ids import new_tool_call_id, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    ContextItem,
    ToolCallItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.sessions import TurnRecord, TurnStatus
from corki.tools import ToolRegistry


def test_runtime_appends_rule_updates_across_steps_turns_and_cold_reopen(tmp_path):
    async def scenario():
        rules = tmp_path / "AGENTS.md"
        rules.write_text("OLD RULE", encoding="utf-8")
        requests, calls = [], []

        class ChangeRules:
            spec = ToolSpec("change_rules", "change project rules", {})

            async def execute(self, call, context):
                calls.append(call)
                rules.write_text("NEW RULE", encoding="utf-8")
                return ToolResult(call.id, call.name, "changed")

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    items = (
                        ToolCallItem(ToolCall(new_tool_call_id(), "change_rules", {}), turn, step),
                    )
                else:
                    items = (AssistantMessageItem("done", turn, step),)
                yield ModelCompleted(items)

            async def aclose(self):
                pass

        def create(thread_id=None):
            registry = ToolRegistry()
            registry.register(ChangeRules())
            return LangGraphRuntime.create(
                settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
                database_path=tmp_path / "sessions.db",
                home_path=tmp_path / ".corki",
                registry=registry,
                model=Model(),
                thread_id=thread_id,
            )

        runtime = create()
        try:
            events = [e async for e in runtime.stream("change rules and continue")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert requests[1].items[: len(requests[0].items)] == requests[0].items
            rules.unlink()
            for _ in range(2):
                events = [e async for e in runtime.stream("continue without project rules")]
                assert isinstance(events[-1], TurnCompleted), events[-1]
            stored = await runtime._repository.load_items(runtime.thread_id)
            contexts = [
                i for i in stored if isinstance(i, ContextItem) and i.key == "project.agents"
            ]
            assert len(contexts) == 3
            assert "OLD RULE" in contexts[0].content
            assert "replace all previously provided AGENTS.md instructions" in contexts[1].content
            assert "NEW RULE" in contexts[1].content
            assert (
                "previously provided AGENTS.md instructions no longer apply" in contexts[2].content
            )
            thread = runtime.thread_id
            await runtime.aclose()
            runtime = create(thread)
            events = [e async for e in runtime.stream("resume unchanged")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert requests[-1].items[: len(stored)] == stored
            rules.write_text("RETURNED RULE", encoding="utf-8")
            events = [e async for e in runtime.stream("use returned rules")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            latest = [
                i
                for i in requests[-1].items
                if isinstance(i, ContextItem) and i.key == "project.agents"
            ]
            assert latest[:3] == contexts
            assert len(latest) == 4 and "RETURNED RULE" in latest[-1].content
            assert "replace all previously" not in latest[-1].content
            assert len(calls) == 1
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_cold_checkpoint_restores_rendered_update_and_comparison_value(tmp_path):
    async def scenario():
        rules = tmp_path / "AGENTS.md"
        rules.write_text("OLD RULE", encoding="utf-8")
        requests = []
        settings = CorkiSettings(working_directory=tmp_path, skills_enabled=False)

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class Sink:
            async def emit(self, event):
                pass

        def create(thread_id=None):
            return LangGraphRuntime.create(
                settings=settings,
                database_path=tmp_path / "sessions.db",
                model=Model(),
                registry=ToolRegistry(),
                thread_id=thread_id,
            )

        runtime = create()
        try:
            events = [e async for e in runtime.stream("first turn")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            rules.write_text("NEW RULE", encoding="utf-8")
            thread, turn = runtime.thread_id, new_turn_id()
            user = UserMessageItem("prepared but not yet sampled", turn)
            await runtime._repository.save_turn(
                TurnRecord(turn, thread, TurnStatus.RUNNING, user.content)
            )
            config = runtime._graph_config(turn)
            await runtime._compiled.ainvoke(
                _initial_state(thread, turn, settings, user),
                config=config,
                context=GraphRunContext(events=Sink()),
                interrupt_before=["call_model"],
            )
            checkpoint = await runtime._compiled.aget_state(config)
            assert checkpoint.next == ("call_model",)
            prepared = await runtime._repository.load_items(thread)
            assert len(requests) == 1
            await runtime.aclose()
            runtime = create(thread)
            events = [e async for e in runtime.resume_pending()]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 2 and requests[-1].items == prepared
            updates = [
                i
                for i in requests[-1].items
                if isinstance(i, ContextItem) and i.key == "project.agents"
            ]
            assert len(updates) == 2 and "replace all previously" in updates[-1].content
            assert "NEW RULE" in updates[-1].snapshot_content
            events = [e async for e in runtime.stream("unchanged after resume")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert [
                i
                for i in requests[-1].items
                if isinstance(i, ContextItem) and i.key == "project.agents"
            ] == updates
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("fail_summary", [False, True])
def test_real_compaction_reinjects_current_snapshot_not_update_notices(tmp_path, fail_summary):
    async def scenario():
        rules = tmp_path / "AGENTS.md"
        rules.write_text("OLD RULE", encoding="utf-8")
        requests, summaries, calls = [], [], []

        class Tool:
            spec = ToolSpec("advance", "update rules then obtain a large observation", {})

            async def execute(self, call, context):
                calls.append(call)
                if len(calls) == 1:
                    rules.write_text("NEW RULE", encoding="utf-8")
                    return ToolResult(call.id, call.name, "updated")
                return ToolResult(call.id, call.name, "OBSERVATION " + "x" * 26000)

        class Model:
            async def stream(self, request):
                turn, step = request.items[-1].turn_id, new_step_id()
                if (
                    isinstance(request.items[-1], UserMessageItem)
                    and "checkpoint compaction" in request.items[-1].content
                ):
                    summaries.append(request)
                    if fail_summary:
                        raise ModelError("summary fixture failed")
                    yield ModelCompleted(
                        (AssistantMessageItem("OBSERVATION retained", turn, step),)
                    )
                    return
                requests.append(request)
                if len(requests) <= 2:
                    items = (ToolCallItem(ToolCall(new_tool_call_id(), "advance", {}), turn, step),)
                else:
                    items = (AssistantMessageItem("done", turn, step),)
                yield ModelCompleted(items)

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Tool())
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                context_window_tokens=14000,
                auto_compact_tokens=5000,
                tool_output_token_limit=5000,
                model_max_retries=0,
            ),
            database_path=tmp_path / "sessions.db",
            registry=registry,
            model=Model(),
        )
        try:
            events = [e async for e in runtime.stream("CURRENT INPUT MUST SURVIVE")]
            assert isinstance(events[-1], TurnFailed if fail_summary else TurnCompleted), events[-1]
            assert len(summaries) == 1 and len(calls) == 2
            summary_rules = [
                i
                for i in summaries[0].items
                if isinstance(i, ContextItem) and i.key == "project.agents"
            ]
            assert len(summary_rules) == 2
            assert "OLD RULE" in summary_rules[0].content
            assert "replace all previously provided" in summary_rules[1].content
            stored = await runtime._repository.load_items(runtime.thread_id)
            assert sum(isinstance(i, CompactionItem) for i in stored) == (not fail_summary)
            assert sum(isinstance(e, ContextCompacted) for e in events) == (not fail_summary)
            assert [i for i in stored if isinstance(i, ContextItem) and i.key == "project.agents"][
                :2
            ] == summary_rules
            if not fail_summary:
                current = [
                    i
                    for i in requests[-1].items
                    if isinstance(i, ContextItem) and i.key == "project.agents"
                ]
                assert len(current) == 1 and "NEW RULE" in current[0].content
                assert "replace all previously" not in current[0].content
                assert current[0].snapshot_content is None
                assert any(
                    isinstance(i, UserMessageItem) and i.content == "CURRENT INPUT MUST SURVIVE"
                    for i in requests[-1].items
                )
                events = [e async for e in runtime.stream("same rules next turn")]
                assert isinstance(events[-1], TurnCompleted), events[-1]
                assert [
                    i
                    for i in requests[-1].items
                    if isinstance(i, ContextItem) and i.key == "project.agents"
                ] == current
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("api_mode", ["responses", "chat_completions"])
def test_context_updates_reach_real_provider_wire_without_rewriting_prefix(
    tmp_path, monkeypatch, api_mode
):
    async def scenario():
        requests = []

        def handle(request):
            requests.append(json.loads(request.content))
            if api_mode == "responses":
                message = {
                    "type": "message",
                    "id": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "done"}],
                }
                data = {
                    "type": "response.completed",
                    "response": {"id": "response", "output": [message]},
                }
                text = "data: " + json.dumps(data) + "\n\n"
            else:
                data = {"choices": [{"delta": {"content": "done"}, "finish_reason": "stop"}]}
                text = "data: " + json.dumps(data) + "\n\ndata: [DONE]\n\n"
            return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                api_mode=api_mode,
                api_key="fixture",
                api_base="https://fixture.invalid/v1",
            ),
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
        )
        rules = tmp_path / "AGENTS.md"
        try:
            for value in ("OLD RULE", "NEW RULE", "", ""):
                if value:
                    rules.write_text(value, encoding="utf-8")
                elif rules.exists():
                    rules.unlink()
                events = [e async for e in runtime.stream("continue")]
                assert isinstance(events[-1], TurnCompleted), events[-1]
            key = "input" if api_mode == "responses" else "messages"
            for previous, current in zip(requests, requests[1:], strict=False):
                assert current[key][: len(previous[key])] == previous[key]
            wire = json.dumps(requests[-1])
            assert "OLD RULE" in wire and "NEW RULE" in wire
            assert wire.count("previously provided AGENTS.md instructions no longer apply") == 1
            assert wire.count("replace all previously provided AGENTS.md instructions") == 1
            assert "snapshot_content" not in wire
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())
