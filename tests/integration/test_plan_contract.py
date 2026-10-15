"""Ordinary plan calls preserve the native data contract through Runtime."""

import asyncio
import json

import pytest

from corki.code_mode.service import CodeModeService
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import PlanUpdated, ToolCallCompleted, TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall


@pytest.mark.parametrize("fail_after_updates", [False, True])
def test_each_nested_plan_update_is_published_with_its_call_identity(tmp_path, fail_after_updates):
    if not CodeModeService.available():
        pytest.skip("install corki[code-mode]")

    async def scenario():
        first = [{"step": "inspect", "status": "in_progress"}]
        second = [{"step": "inspect", "status": "completed"}]

        class Model:
            calls = 0

            async def stream(self, request):
                self.calls += 1
                if self.calls == 1:
                    source = "".join(
                        "await tools.update_plan(" + json.dumps({"plan": plan}) + ");"
                        for plan in (first, second)
                    )
                    if fail_after_updates:
                        source += "throw new Error('after plan updates');"
                    call = ToolCall(
                        new_tool_call_id(),
                        "exec",
                        None,
                        raw_arguments=source,
                        input_kind="freeform",
                    )
                    yield ModelCompleted(
                        (ToolCallItem(call, request.items[-1].turn_id, new_step_id()),)
                    )
                else:
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(tmp_path, skills_enabled=False, tool_mode="code_mode_only"),
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
            model=Model(),
        )
        try:
            events = [event async for event in runtime.stream("update progress twice")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            updates = [event for event in events if isinstance(event, PlanUpdated)]
            assert [event.plan for event in updates] == [tuple(first), tuple(second)]
            calls = [
                event.tool_call_id
                for event in events
                if isinstance(event, ToolCallCompleted) and event.tool_name == "update_plan"
            ]
            assert [event.tool_call_id for event in updates] == calls
            for update in updates:
                completion = next(
                    event
                    for event in events
                    if isinstance(event, ToolCallCompleted)
                    and event.tool_call_id == update.tool_call_id
                )
                assert events.index(update) < events.index(completion)
            outer = [
                event
                for event in events
                if isinstance(event, ToolCallCompleted) and event.tool_name == "exec"
            ]
            assert len(outer) == 1 and outer[0].is_error == fail_after_updates
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "plan",
    [
        [],
        [{"step": "", "status": "pending"}],
        [
            {"step": "  first  ", "status": "in_progress"},
            {"step": "second", "status": "in_progress"},
        ],
    ],
)
def test_plan_data_is_not_rejected_by_extra_semantic_constraints(tmp_path, plan):
    async def scenario():
        class Model:
            calls = 0

            async def stream(self, request):
                self.calls += 1
                turn, step = request.items[-1].turn_id, new_step_id()
                if self.calls % 2:
                    args = {"plan": plan if self.calls == 1 else []}
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(new_tool_call_id(), "update_plan", args), turn, step
                            ),
                        )
                    )
                else:
                    result = next(
                        i for i in reversed(request.items) if isinstance(i, ToolResultItem)
                    )
                    assert not result.is_error
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        settings = CorkiSettings(working_directory=tmp_path, skills_enabled=False)
        database = tmp_path / "history.db"
        model = Model()
        runtime = await LangGraphRuntime.acreate(
            settings=settings, database_path=database, home_path=tmp_path / "home", model=model
        )
        try:
            for prompt, expected in (("record plan", tuple(plan)), ("clear plan", ())):
                events = [e async for e in runtime.stream(prompt)]
                assert isinstance(events[-1], TurnCompleted)
                updates = [e for e in events if isinstance(e, PlanUpdated)]
                assert len(updates) == 1 and updates[0].plan == expected
            history = await runtime._repository.load_items(runtime.thread_id)
            results = [i for i in history if isinstance(i, ToolResultItem)]
            assert [i.state_update.plan for i in results] == [tuple(plan), ()]
            assert model.calls == 4
        finally:
            await runtime.aclose()
        cold = await LangGraphRuntime.acreate(
            settings=settings,
            database_path=database,
            home_path=tmp_path / "home",
            thread_id=runtime.thread_id,
            model=Model(),
        )
        try:
            assert await cold._repository.load_items(cold.thread_id) == history
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("explanation", [None, "", "   ", "  User-visible reason  "])
def test_plan_acknowledgement_is_separate_from_explanation(tmp_path, mode, explanation):
    if mode != "direct" and not CodeModeService.available():
        pytest.skip("install corki[code-mode]")

    async def scenario():
        class Model:
            calls = 0

            async def stream(self, request):
                self.calls += 1
                turn, step = request.items[-1].turn_id, new_step_id()
                if self.calls == 1:
                    args = {"plan": []}
                    if explanation is not None:
                        args["explanation"] = explanation
                    if mode == "direct":
                        call = ToolCall(new_tool_call_id(), "update_plan", args)
                    else:
                        call = ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            raw_arguments="const value = await tools.update_plan("
                            + json.dumps(args)
                            + "); text(JSON.stringify(value));",
                            input_kind="freeform",
                        )
                    yield ModelCompleted((ToolCallItem(call, turn, step),))
                else:
                    result = next(
                        i for i in reversed(request.items) if isinstance(i, ToolResultItem)
                    )
                    assert not result.is_error
                    if mode == "direct":
                        assert result.content == "Plan updated"
                    else:
                        assert result.content.rstrip().endswith("{}")
                        assert "User-visible reason" not in result.content
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, tool_mode=mode
            ),
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
            model=Model(),
        )
        try:
            events = [e async for e in runtime.stream("update plan")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            updates = [e for e in events if isinstance(e, PlanUpdated)]
            assert len(updates) == 1
            assert updates[0].explanation == explanation
            history = await runtime._repository.load_items(runtime.thread_id)
            if mode == "direct":
                result = next(i for i in history if isinstance(i, ToolResultItem))
                assert result.state_update.plan_explanation == explanation
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
