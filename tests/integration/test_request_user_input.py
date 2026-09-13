"""Real ordinary-function requests wait for owned replies, never implicit consent."""

import asyncio
import json

import pytest

from corki.code_mode.service import CodeModeService
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCancelled, TurnCompleted, UserInputRequested
from corki.protocol.items import (
    AssistantMessageItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.session_source import SessionSource
from corki.protocol.tools import ToolCall

QUESTIONS = [
    {
        "id": "scope",
        "header": "Scope",
        "question": "Which scope?",
        "options": [
            {"label": "Small (Recommended)", "description": "Ship a small change."},
            {"label": "Large", "description": "Ship all changes."},
        ],
    }
]


class Model:
    def __init__(self, questions=QUESTIONS, *, nested=False):
        self.requests = []
        self.questions = questions
        self.nested = nested

    async def stream(self, request):
        self.requests.append(request)
        turn, step = request.items[-1].turn_id, new_step_id()
        if len(self.requests) == 1:
            call = ToolCall("question-1", "request_user_input", {"questions": self.questions})
            if self.nested:
                call = ToolCall(
                    "question-1",
                    "exec",
                    None,
                    input_kind="freeform",
                    raw_arguments="await tools.request_user_input("
                    + json.dumps({"questions": self.questions})
                    + ");",
                )
            yield ModelCompleted(
                (
                    ToolCallItem(
                        call,
                        turn,
                        step,
                    ),
                )
            )
        else:
            yield ModelCompleted((AssistantMessageItem("done", turn, step),))

    async def aclose(self):
        pass


def make_runtime(tmp_path, model, source=None, **settings):
    return LangGraphRuntime.create(
        settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False, **settings),
        database_path=tmp_path / "history.db",
        home_path=tmp_path / "home",
        model=model,
        **({"session_source": source} if source is not None else {}),
    )


@pytest.mark.parametrize("tool_mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("mode", ["plan", "default"])
def test_question_reply_is_a_tool_result_not_a_user_message(tmp_path, tool_mode, mode):
    if tool_mode != "direct" and not CodeModeService.available():
        pytest.skip("install corki[code-mode]")

    async def scenario():
        model = Model()
        runtime = make_runtime(
            tmp_path,
            model,
            tool_mode=tool_mode,
            collaboration_mode=mode,
            default_mode_request_user_input=mode == "default",
        )
        response = {"answers": {"scope": {"answers": ["a custom scope"]}}}
        events = []
        try:
            async for event in runtime.stream("ask me"):
                events.append(event)
                if isinstance(event, UserInputRequested):
                    assert len(model.requests) == 1
                    assert event.is_blocking == (mode == "plan")
                    assert event.questions[0].is_other
                    assert not runtime.respond_user_input("wrong", event.call_id, response)
                    assert not runtime.cancel_user_input("wrong", event.call_id)
                    assert not runtime.cancel_user_input(event.turn_id, "wrong")
                    assert not runtime.respond_user_input(event.turn_id, "wrong", response)
                    with pytest.raises(ValueError):
                        runtime.respond_user_input(event.turn_id, event.call_id, {"answers": []})
                    assert runtime.respond_user_input(event.turn_id, event.call_id, response)
                    assert not runtime.cancel_user_input(event.turn_id, event.call_id)
                    assert not runtime.respond_user_input(event.turn_id, event.call_id, response)
                    response["answers"]["scope"]["answers"][0] = "mutated after submission"
            assert isinstance(events[-1], TurnCompleted)
            assert sum(isinstance(e, UserInputRequested) for e in events) == 1
            assert "request_user_input" in {tool.name for tool in model.requests[0].tools}
            result = next(
                i for i in reversed(model.requests[1].items) if isinstance(i, ToolResultItem)
            )
            assert not result.is_error
            assert json.loads(result.content) == {
                "answers": {"scope": {"answers": ["a custom scope"]}}
            }
            history = await runtime._repository.load_items(runtime.thread_id)
            assert [i.content for i in history if isinstance(i, UserMessageItem)] == ["ask me"]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_question_is_not_exposed_to_nested_code_mode(tmp_path):
    if not CodeModeService.available():
        pytest.skip("install corki[code-mode]")

    async def scenario():
        model = Model(nested=True)
        runtime = make_runtime(
            tmp_path, model, collaboration_mode="plan", tool_mode="code_mode_only"
        )
        try:
            events = [e async for e in runtime.stream("ask")]
            assert isinstance(events[-1], TurnCompleted)
            assert not any(isinstance(e, UserInputRequested) for e in events)
            result = next(
                i for i in reversed(model.requests[1].items) if isinstance(i, ToolResultItem)
            )
            assert result.is_error
            assert "TypeError: not a function" in result.content
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["cancel", "close", "dismiss"])
def test_waiting_question_does_not_outlive_its_turn(tmp_path, action):
    async def scenario():
        ready = asyncio.Event()
        model = Model()
        runtime = make_runtime(tmp_path, model, collaboration_mode="plan")
        requests = []

        async def consume():
            events = []
            try:
                async for event in runtime.stream("ask"):
                    events.append(event)
                    if isinstance(event, UserInputRequested):
                        requests.append(event)
                        ready.set()
            except asyncio.CancelledError:
                # Runtime emits its terminal and preserves cancellation control
                # flow; a cancelled tool must not become a success Observation.
                assert action in ("cancel", "close")
            return events

        work = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(ready.wait(), 5)
            run = runtime._active_run
            event = requests[0]
            assert len(model.requests) == 1 and not work.done()
            if action == "close":
                await runtime.aclose()
            elif action == "cancel":
                await runtime.cancel_active()
            else:
                assert runtime.respond_user_input(event.turn_id, event.call_id, None)
            events = await asyncio.wait_for(work, 5)
            assert isinstance(events[-1], TurnCompleted if action == "dismiss" else TurnCancelled)
            assert run.user_input._pending is None
            assert not runtime.respond_user_input(event.turn_id, event.call_id, {"answers": {}})
            if action == "dismiss":
                result = next(
                    i for i in reversed(model.requests[1].items) if isinstance(i, ToolResultItem)
                )
                assert result.is_error and "cancelled before receiving" in result.content
            else:
                assert len(model.requests) == 1
        finally:
            await runtime.aclose()
            await asyncio.gather(work, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "settings,questions,error",
    [
        ({}, QUESTIONS, "unavailable in Default mode"),
        ({"collaboration_mode": "plan"}, [{**QUESTIONS[0], "options": []}], "non-empty options"),
        ({"request_user_input_enabled": False}, QUESTIONS, "not advertised"),
        (
            {
                "collaboration_mode": "plan",
                "source": SessionSource.internal("memory_consolidation"),
            },
            QUESTIONS,
            "only be used by the root thread",
        ),
    ],
)
def test_question_rejection_returns_an_observation(tmp_path, settings, questions, error):
    async def scenario():
        model = Model(questions)
        runtime = make_runtime(tmp_path, model, **settings)
        try:
            events = [e async for e in runtime.stream("ask")]
            assert isinstance(events[-1], TurnCompleted)
            assert not any(isinstance(e, UserInputRequested) for e in events)
            result = next(
                i for i in reversed(model.requests[1].items) if isinstance(i, ToolResultItem)
            )
            assert result.is_error and error in result.content
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
