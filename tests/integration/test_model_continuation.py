"""Map Codex run_turn's end_turn/needs_follow_up contract onto the real Runtime."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.models import ModelCompleted, OpenAIResponsesModel, resolve_capabilities
from corki.protocol.events import TurnCompleted, TurnFailed
from corki.protocol.ids import new_turn_id
from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id
from corki.protocol.tools import ToolResult, ToolSpec
from corki.sessions import TurnRecord, TurnStatus
from corki.storage import SQLiteSessionRepository
from corki.tools import ToolRegistry


class EchoTool:
    spec = ToolSpec("echo", "Echo a fixture", {"type": "object", "properties": {}})

    def __init__(self):
        self.calls = 0

    async def execute(self, call, context):
        self.calls += 1
        return ToolResult(call.id, call.name, "echo observed")


def test_completed_model_event_ends_sampling_and_closes_stream(tmp_path: Path):
    class TerminalThenBrokenTail:
        samples = 0
        stream_closed = 0

        async def stream(self, request):
            self.samples += 1
            try:
                yield ModelCompleted(
                    (AssistantMessageItem("finished", request.items[-1].turn_id, new_step_id()),)
                )
                raise RuntimeError("unread events after terminal response")
            finally:
                self.stream_closed += 1

        async def aclose(self):
            pass

    async def scenario():
        model = TerminalThenBrokenTail()
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "terminal-tail.db",
            model=model,
            registry=ToolRegistry(),
        )
        try:
            events = [event async for event in runtime.stream("finish")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert events[-1].final_answer == "finished"
            assert model.samples == 1
            assert model.stream_closed == 1
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def output(kind):
    if kind == "text":
        return [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "interim"}],
            }
        ]
    if kind == "reasoning":
        return [
            {
                "type": "reasoning",
                "id": "rs",
                "encrypted_content": "encrypted-fixture",
                "summary": [{"type": "summary_text", "text": "thinking"}],
            }
        ]
    if kind == "tool":
        return [
            *output("text"),
            {
                "type": "function_call",
                "id": "fc",
                "call_id": "c",
                "name": "echo",
                "arguments": "{}",
            },
        ]
    return []


async def run_responses(tmp_path, responder, *, max_steps=4):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        response = {"id": f"response-{len(requests)}", **responder(len(requests))}
        event = {"type": "response.completed", "response": response}
        return httpx.Response(200, text=f"data: {json.dumps(event)}\n\n")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    model = OpenAIResponsesModel(
        api_key="fixture",
        base_url="https://fixture.test/v1",
        capabilities=resolve_capabilities(base_url="https://fixture.test/v1", api_mode="responses"),
        client=client,
        retry_base_seconds=0,
    )
    registry, echo = ToolRegistry(), EchoTool()
    registry.register(echo)
    runtime = await LangGraphRuntime.acreate(
        settings=CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            max_steps=max_steps,
            model_retry_base_seconds=0.001,
        ),
        database_path=tmp_path / "continuation.db",
        model=model,
        registry=registry,
    )
    try:
        events = [event async for event in runtime.stream("finish the task")]
        return events, requests, echo.calls
    finally:
        await runtime.aclose()
        await client.aclose()


@pytest.mark.parametrize(
    "kind,end_turn", [("text", False), ("reasoning", False), ("empty", False), ("tool", True)]
)
def test_completed_response_continues_when_requested_or_tools_pending(
    tmp_path: Path, kind, end_turn
):
    def responder(index):
        if index == 1:
            return {"output": output(kind), "end_turn": end_turn}
        # An absent flag must reset the previous explicit false.
        return {"output": output("text")}

    events, requests, calls = asyncio.run(run_responses(tmp_path, responder))
    assert isinstance(events[-1], TurnCompleted), events[-1]
    assert len(requests) == 2, "intermediate completed response was treated as end of turn"
    assert sum(isinstance(event, (TurnCompleted, TurnFailed)) for event in events) == 1
    assert calls == (1 if kind == "tool" else 0)
    assert sum("finish the task" in json.dumps(item) for item in requests[1]["input"]) == 1
    if kind == "text":
        assert any(item.get("role") == "assistant" for item in requests[1]["input"])
    elif kind == "reasoning":
        assert any(item.get("type") == "reasoning" for item in requests[1]["input"])
    elif kind == "tool":
        assert any(item.get("output") == "echo observed" for item in requests[1]["input"])


@pytest.mark.parametrize("kind", ["text", "reasoning", "empty"])
def test_explicit_continuation_is_bounded_by_model_step_budget(tmp_path: Path, kind):
    events, requests, _ = asyncio.run(
        run_responses(tmp_path, lambda _: {"output": output(kind), "end_turn": False}, max_steps=2)
    )
    assert len(requests) == 2
    assert isinstance(events[-1], TurnFailed)
    assert "model step limit" in events[-1].error
    assert not any(isinstance(event, TurnCompleted) for event in events)


@pytest.mark.parametrize("end_turn", ["false", 0, 1, [], {}])
def test_malformed_continuation_flag_exhausts_stream_retries_without_success(
    tmp_path: Path, end_turn
):
    events, requests, _ = asyncio.run(
        run_responses(tmp_path, lambda _: {"output": output("text"), "end_turn": end_turn})
    )
    assert len(requests) == 6
    assert isinstance(events[-1], TurnFailed)
    assert "end_turn" in events[-1].error


@pytest.mark.parametrize("completion", [{}, {"end_turn": None}, {"end_turn": True}])
def test_valid_empty_completed_response_can_end_turn(tmp_path: Path, completion):
    events, requests, _ = asyncio.run(run_responses(tmp_path, lambda _: completion))
    assert len(requests) == 1
    assert isinstance(events[-1], TurnCompleted), events[-1]


@pytest.mark.parametrize("terminal_kind", ["empty", "reasoning", "text", "multiple", "whitespace"])
def test_codex_tool_then_continuation_then_completion_sequence(tmp_path: Path, terminal_kind):
    """current_time_reminders_can_follow_only_user_or_tool_outputs, without time hooks."""
    terminal_output = output(terminal_kind)
    expected = "interim" if terminal_kind == "text" else ""
    if terminal_kind in {"multiple", "whitespace"}:
        texts = (
            ["first terminal", " ", "last terminal", "\n"]
            if terminal_kind == "multiple"
            else [" \n"]
        )
        terminal_output = [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }
            for text in texts
        ]
        expected = "last terminal" if terminal_kind == "multiple" else ""
    responses = [
        {"output": output("tool")},
        {"output": output("text"), "end_turn": False},
        {"output": terminal_output},
    ]
    events, requests, calls = asyncio.run(
        run_responses(tmp_path, lambda index: responses[index - 1])
    )
    assert isinstance(events[-1], TurnCompleted), events[-1]
    # Final answer belongs to the terminal sampling step. Earlier commentary
    # is retained in history but cannot fill an empty terminal answer.
    assert events[-1].final_answer == expected
    assert sum(isinstance(event, (TurnCompleted, TurnFailed)) for event in events) == 1
    assert len(requests) == 3
    assert calls == 1
    assert sum("finish the task" in json.dumps(item) for item in requests[2]["input"]) == 1
    assert sum(item.get("type") == "function_call_output" for item in requests[2]["input"]) == 1


@pytest.mark.parametrize("checkpoint", [False, True])
@pytest.mark.parametrize("empty", [False, True])
def test_continuation_survives_committed_step_and_real_checkpoint_recovery(
    tmp_path: Path, checkpoint, empty
):
    class AnswerModel:
        def __init__(self):
            self.requests = []

        async def stream(self, request):
            self.requests.append(request)
            yield ModelCompleted(
                (AssistantMessageItem("finished", request.items[-1].turn_id, new_step_id()),)
            )

        async def aclose(self):
            pass

    class NullSink:
        async def emit(self, event):
            pass

    async def scenario():
        settings = CorkiSettings(working_directory=tmp_path, skills_enabled=False)
        database = tmp_path / "recovery.db"
        repository = SQLiteSessionRepository(database)
        first_model = AnswerModel()
        first = await LangGraphRuntime.acreate(
            settings=settings,
            database_path=database,
            repository=repository,
            model=first_model,
            registry=ToolRegistry(),
        )
        entered_evaluate = asyncio.Event()

        async def blocked_evaluate(state, runtime):
            entered_evaluate.set()
            await asyncio.Event().wait()

        if checkpoint:
            # Block after call_model's durable result and graph state are committed.
            first._graph._evaluate = blocked_evaluate
        await first._ensure_ready()
        turn = new_turn_id()
        user = UserMessageItem("continue after restart", turn)
        await repository.save_turn(
            TurnRecord(turn, first.thread_id, TurnStatus.RUNNING, user.content)
        )
        await repository.append_items(first.thread_id, (user,))
        completed = ModelCompleted(
            () if empty else (AssistantMessageItem("interim", turn, new_step_id()),),
            end_turn=False,
        )
        await repository.commit_model_step(first.thread_id, turn, 0, completed)
        if checkpoint:
            invocation = asyncio.create_task(
                first._compiled.ainvoke(
                    _initial_state(first.thread_id, turn, settings, user),
                    context=GraphRunContext(events=NullSink()),
                    config=first._graph_config(turn),
                )
            )
            try:
                await asyncio.wait_for(entered_evaluate.wait(), timeout=3)
            finally:
                invocation.cancel()
                await asyncio.gather(invocation, return_exceptions=True)
            # The graph joins asynchronous checkpoint writes on exit. Entering
            # evaluate alone does not guarantee its predecessor's write finished.
            saved = await first._checkpointer.aget_tuple(first._graph_config(turn))
            assert saved.checkpoint["channel_values"]["model_end_turn"] is False
            assert saved.checkpoint["channel_values"]["step_count"] == 1
        assert first_model.requests == []
        await first.aclose()
        model = AnswerModel()
        resumed = await LangGraphRuntime.acreate(
            settings=settings,
            database_path=database,
            thread_id=first.thread_id,
            model=model,
            registry=ToolRegistry(),
        )
        try:
            events = [event async for event in resumed.resume_pending()]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert events[-1].final_answer == "finished"
            assert len(model.requests) == 1, "committed step was resampled or continuation was lost"
            assert sum(isinstance(item, UserMessageItem) for item in model.requests[0].items) == 1
            loaded = await resumed._repository.load_model_step(first.thread_id, turn, 0)
            assert loaded == completed
            history = await resumed._repository.load_items(first.thread_id)
            assert sum(
                isinstance(item, AssistantMessageItem) and item.content == "interim"
                for item in history
            ) == (0 if empty else 1)
        finally:
            await resumed.aclose()

    asyncio.run(scenario())
