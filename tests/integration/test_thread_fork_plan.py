"""Plan history is inherited data, not a new plan-update command."""

import asyncio

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import PlanUpdated, TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import (
    AssistantMessageItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry
from corki.tools.builtin.plan import UpdatePlanTool


@pytest.mark.parametrize("compact", [False, True])
def test_plan_history_survives_fork_without_republishing_old_updates(tmp_path, compact):
    async def scenario():
        plans = (
            ({"step": "inspect", "status": "in_progress"},),
            ({"step": "inspect", "status": "completed"},),
        )
        requests = []
        summaries = []
        summary_prompt = "SUMMARIZE_PLAN_HISTORY"

        class Model:
            def __init__(self, phase):
                self.phase, self.calls = phase, 0

            async def stream(self, request):
                user = next(i for i in reversed(request.items) if isinstance(i, UserMessageItem))
                if user.content == summary_prompt:
                    summaries.append(request)
                    yield ModelCompleted(
                        (AssistantMessageItem("PLAN SUMMARY", user.turn_id, new_step_id()),)
                    )
                    return
                self.calls += 1
                requests.append(request)
                prior = [
                    i
                    for i in request.items
                    if isinstance(i, ToolCallItem) and i.call.name == "update_plan"
                ]
                if self.phase and self.calls == 1:
                    assert [tuple(i.call.arguments["plan"]) for i in prior] == list(
                        plans[1 if compact else 0 : self.phase]
                    )
                if self.phase < 2 and self.calls == 1:
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(
                                    new_tool_call_id(),
                                    "update_plan",
                                    {
                                        "plan": list(plans[self.phase]),
                                        "explanation": f"phase {self.phase}",
                                    },
                                ),
                                request.items[-1].turn_id,
                                new_step_id(),
                            ),
                        )
                    )
                else:
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        async def create(phase, **kwargs):
            registry = ToolRegistry()
            registry.register(UpdatePlanTool())
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    tmp_path,
                    skills_enabled=False,
                    plugins_enabled=False,
                    compact_prompt=summary_prompt,
                ),
                model=Model(phase),
                registry=registry,
                database_path=tmp_path / "state.db",
                home_path=tmp_path,
                **kwargs,
            )

        source = await create(0)
        try:
            events = [e async for e in source.stream("SOURCE")]
            assert isinstance(events[-1], TurnCompleted)
            assert [e.plan for e in events if isinstance(e, PlanUpdated)] == [plans[0]]
            if compact:
                before = await source.load_display_snapshot()
                events = [e async for e in source.compact()]
                assert isinstance(events[-1], TurnCompleted)
                assert not any(isinstance(e, PlanUpdated) for e in events)
                after = await source.load_display_snapshot()
                assert after.items[: len(before.items)] == before.items
                assert len(summaries) == 1
                assert any(
                    isinstance(i, ToolCallItem) and i.call.name == "update_plan"
                    for i in summaries[0].items
                )
            original = await source.load_display_snapshot()
            source_id = source.thread_id
        finally:
            await source.aclose()
        branch = await create(1, fork_from_thread_id=source_id)
        try:
            assert [e async for e in branch.resume_pending()] == []
            copied = await branch.load_display_snapshot()
            old = next(i for i in original.items if isinstance(i, ToolResultItem))
            new = next(i for i in copied.items if isinstance(i, ToolResultItem))
            assert new.state_update == old.state_update
            assert new.call_id != old.call_id
            events = [e async for e in branch.stream("BRANCH")]
            assert isinstance(events[-1], TurnCompleted)
            updates = [e for e in events if isinstance(e, PlanUpdated)]
            assert [e.plan for e in updates] == [plans[1]]
            assert updates[0].tool_call_id not in {old.call_id, new.call_id}
            assert await branch._repository.load_display_snapshot(source_id) == original
            branch_id = branch.thread_id
        finally:
            await branch.aclose()
        cold = await create(2, thread_id=branch_id)
        try:
            assert [e async for e in cold.resume_pending()] == []
            events = [e async for e in cold.stream("COLD")]
            assert isinstance(events[-1], TurnCompleted)
            assert not any(isinstance(e, PlanUpdated) for e in events)
            assert len(requests) == 5
            assert len(summaries) == int(compact)
            if compact:
                assert "PLAN SUMMARY" in str(requests[-1].items)
            assert await cold._repository.load_display_snapshot(source_id) == original
        finally:
            await cold.aclose()

    asyncio.run(scenario())
