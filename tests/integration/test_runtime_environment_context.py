import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from xml.etree import ElementTree

import httpx
import pytest

from corki import http_client
from corki.config import CorkiSettings
from corki.config.managed_mcp import MCPRequirementsLayer, compose_mcp_requirements
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
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


def freeze_clock(monkeypatch):
    day = [8]
    monkeypatch.setattr("corki.context.local_time.timezone_name", lambda: "UTC")
    monkeypatch.setattr(
        "corki.context.builder.datetime",
        SimpleNamespace(
            now=lambda: SimpleNamespace(astimezone=lambda: datetime(2026, 9, day[0], tzinfo=UTC))
        ),
    )
    return day


def environments(items):
    return [i for i in items if isinstance(i, ContextItem) and i.key == "environment.primary"]


@pytest.mark.parametrize("compact", [False, True])
def test_runtime_environment_delta_steps_compaction_and_cold_reopen(tmp_path, monkeypatch, compact):
    day = freeze_clock(monkeypatch)

    async def scenario():
        requests, summaries, calls = [], [], []

        class Tool:
            spec = ToolSpec("advance", "advance fixture clock", {})

            async def execute(self, call, context):
                calls.append(call)
                day[0] = 9
                return ToolResult(call.id, call.name, "changed" if len(calls) == 1 else "x" * 26000)

        class Model:
            async def stream(self, request):
                turn, step = request.items[-1].turn_id, new_step_id()
                if (
                    isinstance(request.items[-1], UserMessageItem)
                    and "checkpoint compaction" in request.items[-1].content
                ):
                    summaries.append(request)
                    yield ModelCompleted((AssistantMessageItem("clock advanced", turn, step),))
                    return
                requests.append(request)
                item = (
                    ToolCallItem(ToolCall(new_tool_call_id(), "advance", {}), turn, step)
                    if len(requests) <= (2 if compact else 1)
                    else AssistantMessageItem("done", turn, step)
                )
                yield ModelCompleted((item,))

            async def aclose(self):
                pass

        def create(thread_id=None):
            registry = ToolRegistry()
            registry.register(Tool())
            return LangGraphRuntime.create(
                settings=CorkiSettings(
                    working_directory=tmp_path,
                    skills_enabled=False,
                    context_window_tokens=14000,
                    auto_compact_tokens=5000,
                    tool_output_token_limit=5000,
                ),
                database_path=tmp_path / "sessions.db",
                home_path=tmp_path / ".corki",
                registry=registry,
                model=Model(),
                thread_id=thread_id,
            )

        runtime = create()
        try:
            events = [e async for e in runtime.stream("CURRENT INPUT MUST SURVIVE")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            old, delta = environments(requests[1].items)
            assert "<current_date>2026-09-08</current_date>" in old.content
            assert delta.content == (
                "<environment_context>\n  <current_date>2026-09-09</current_date>\n"
                "  <timezone>UTC</timezone>\n"
                '  <filesystem><permission_profile type="managed">'
                '<file_system type="restricted"><entry access="read">'
                "<special>:root</special></entry></file_system>"
                "</permission_profile></filesystem>\n</environment_context>\n"
            )
            stored = await runtime._repository.load_items(runtime.thread_id)
            assert environments(stored)[:2] == [old, delta]
            assert bool(summaries) == compact
            assert sum(isinstance(i, CompactionItem) for i in stored) == compact
            if compact:
                (current,) = environments(requests[-1].items)
                assert ElementTree.fromstring(current.content).findtext("cwd") == str(tmp_path)
                assert current.content == delta.snapshot_content
                assert current.snapshot_content is None
            assert any(
                isinstance(i, UserMessageItem) and i.content == "CURRENT INPUT MUST SURVIVE"
                for i in requests[-1].items
            )
            before = environments(requests[-1].items)
            thread = runtime.thread_id
            await runtime.aclose()
            runtime = create(thread)
            events = [e async for e in runtime.stream("unchanged after reopen")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert environments(requests[-1].items) == before
            assert environments(await runtime._repository.load_items(thread)) == environments(
                stored
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("api_mode", ["responses", "chat_completions"])
def test_environment_deltas_reach_provider_wire(tmp_path, monkeypatch, api_mode):
    day = freeze_clock(monkeypatch)

    async def scenario():
        requests = []

        def handle(request):
            requests.append(json.loads(request.content))
            if api_mode == "responses":
                data = {
                    "type": "response.completed",
                    "response": {
                        "id": "response",
                        "output": [
                            {
                                "type": "message",
                                "id": "message",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": "done"}],
                            }
                        ],
                    },
                }
                body = "data: " + json.dumps(data) + "\n\n"
            else:
                data = {"choices": [{"delta": {"content": "done"}, "finish_reason": "stop"}]}
                body = "data: " + json.dumps(data) + "\n\ndata: [DONE]\n\n"
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        monkeypatch.setattr(http_client, "OwnedHTTPClient", lambda **kwargs: client)
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
        try:
            for value in (8, 9, 9):
                day[0] = value
                events = [e async for e in runtime.stream("continue")]
                assert isinstance(events[-1], TurnCompleted), events[-1]
            key = "input" if api_mode == "responses" else "messages"
            for previous, current in zip(requests, requests[1:], strict=False):
                assert current[key][: len(previous[key])] == previous[key]
            wire = json.dumps(requests[-1])
            assert wire.count("<environment_context>") == 2
            assert wire.count("<cwd>") == 1
            assert "no longer apply" not in wire
            assert "snapshot_state" not in wire and "snapshot_content" not in wire
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


def test_runtime_environment_gate_survives_cold_reopen_and_reenable(tmp_path, monkeypatch):
    freeze_clock(monkeypatch)

    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        thread = None
        histories = []
        for enabled in (False, True, False, False, True):
            runtime = LangGraphRuntime.create(
                settings=CorkiSettings(
                    working_directory=tmp_path,
                    skills_enabled=False,
                    include_environment_context=enabled,
                ),
                database_path=tmp_path / "sessions.db",
                home_path=tmp_path / ".corki",
                registry=ToolRegistry(),
                model=Model(),
                thread_id=thread,
            )
            try:
                events = [e async for e in runtime.stream("continue")]
                assert isinstance(events[-1], TurnCompleted), events[-1]
                thread = runtime.thread_id
                histories.append(environments(await runtime._repository.load_items(thread)))
            finally:
                await runtime.aclose()
        assert [len(environments(r.items)) for r in requests] == [0, 1, 1, 1, 2]
        assert [len(h) for h in histories] == [0, 1, 2, 2, 3]
        assert histories[2][-1].is_snapshot_only
        assert histories[2][-1].snapshot_content == ""
        assert histories[4][-1].content == histories[1][0].content
        assert "no longer apply" not in "".join(i.content for h in histories for i in h)

    asyncio.run(scenario())


@pytest.mark.parametrize("change_policy", [False, True])
def test_environment_delta_checkpoint_replays_frozen_request_then_refreshes(
    tmp_path, monkeypatch, change_policy
):
    day = freeze_clock(monkeypatch)

    async def scenario():
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
                registry=ToolRegistry(),
                model=Model(),
                thread_id=thread_id,
                mcp_requirements=compose_mcp_requirements(
                    (
                        MCPRequirementsLayer(
                            "host", 'additional_developer_instructions = "NEW POLICY"'
                        ),
                    )
                )
                if thread_id is not None and change_policy
                else None,
            )

        runtime = create()
        try:
            events = [e async for e in runtime.stream("first turn")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            day[0] = 9
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
            assert (await runtime._compiled.aget_state(config)).next == ("call_model",)
            prepared = await runtime._repository.load_items(thread)
            delta = environments(prepared)[-1]
            assert "<cwd>" not in delta.content
            assert "<cwd>" in delta.snapshot_content
            assert "2026-09-09" in delta.content
            await runtime.aclose()
            day[0] = 10
            runtime = create(thread)
            events = [e async for e in runtime.resume_pending()]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            visible = tuple(
                i for i in prepared if not isinstance(i, ContextItem) or not i.is_snapshot_only
            )
            # Ordinary replay preserves the frozen items without adding private metadata.
            expected_items = visible
            assert len(requests) == 2
            restored = requests[-1].items
            if change_policy:
                policies = tuple(
                    i
                    for i in restored
                    if isinstance(i, ContextItem) and i.key == "managed_developer_instructions"
                )
                assert len(policies) == 1 and "NEW POLICY" in policies[0].content
                restored = tuple(i for i in restored if i not in policies)
            assert restored == expected_items
            assert all(
                item.response_item_metadata_json is None
                for item in visible
                if isinstance(item, AssistantMessageItem)
            )
            events = [e async for e in runtime.stream("new turn refresh")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            current = environments(requests[-1].items)
            assert len(current) == 3
            assert current[-2] == delta
            assert "2026-09-10" in current[-1].content and "<cwd>" not in current[-1].content
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
