"""Goal lifecycle through real runtime/SQLite with deterministic model responses."""

import asyncio

import pytest
from test_thread_settings_update import make_runtime, settings

from corki.models import ModelCompleted, ModelError, ModelErrorKind
from corki.models.types import ModelUsage
from corki.protocol.events import TurnCompleted, TurnFailed, TurnStarted
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    ToolCallItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall


class GoalModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        turn, step = request.items[-1].turn_id, new_step_id()
        item = (
            ToolCallItem(ToolCall(f"call-{len(self.requests)}", *response), turn, step)
            if isinstance(response, tuple)
            else AssistantMessageItem(response, turn, step)
        )
        yield ModelCompleted(
            (item,), usage=ModelUsage(input_tokens=100, output_tokens=20, cached_tokens=80)
        )

    async def aclose(self):
        pass


def test_goal_continues_and_model_completes_without_faking_user_input(tmp_path):
    async def scenario():
        model = GoalModel(["first part", ("update_goal", {"status": "complete"}), "verified done"])
        runtime = await make_runtime(tmp_path, model)
        try:
            await runtime.goal_command("create", objective="Finish both parts", token_budget=1000)
            events = [e async for e in runtime.continue_goal()]
            assert len([e for e in events if isinstance(e, TurnStarted)]) == 2
            assert isinstance(events[-1], TurnCompleted)
            result = await runtime.goal_command()
            assert result["goal"]["status"] == "complete"
            assert (
                result["goal"]["tokens_used"] == 80
            )  # excludes cached input and post-completion wrap-up
            assert result["remaining_tokens"] == 920
            items = await runtime._repository.load_items(runtime.thread_id)
            assert not any(isinstance(i, UserMessageItem) for i in items)
            assert (
                sum(isinstance(i, ContextItem) and i.key == "goal.continuation" for i in items) == 2
            )
            assert "Finish both parts" in str(model.requests[0].items)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "kind,status",
    [(ModelErrorKind.PROTOCOL, "blocked"), (ModelErrorKind.USAGE_LIMIT, "usage_limited")],
)
def test_goal_stops_on_terminal_model_error(tmp_path, kind, status):
    async def scenario():
        model = GoalModel([ModelError("failed", kind=kind)])
        runtime = await make_runtime(tmp_path, model)
        try:
            await runtime.goal_command("create", objective="test error")
            events = [e async for e in runtime.continue_goal()]
            assert isinstance(events[-1], TurnFailed)
            assert (await runtime.goal_command())["goal"]["status"] == status
            assert len(model.requests) == 1
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_budget_stops_and_resume_cannot_bypass_budget(tmp_path):
    async def scenario():
        runtime = await make_runtime(tmp_path, GoalModel(["part one"]))
        try:
            await runtime.goal_command("create", objective="finish", token_budget=30)
            events = [e async for e in runtime.continue_goal()]
            assert isinstance(events[-1], TurnCompleted)
            result = await runtime.goal_command("resume")
            assert result["goal"]["status"] == "budget_limited"
            assert result["goal"]["tokens_used"] == 40
            assert (await runtime.goal_command("pause"))["goal"]["status"] == "budget_limited"
            assert [e async for e in runtime.continue_goal()] == []
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_three_empty_automatic_turns_block(tmp_path):
    async def scenario():
        model = GoalModel(["", "", ""])
        runtime = await make_runtime(tmp_path, model)
        try:
            await runtime.goal_command("create", objective="do work")
            await asyncio.wait_for(collect(runtime.continue_goal()), 10)
            assert len(model.requests) == 3
            assert (await runtime.goal_command())["goal"]["status"] == "blocked"
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


async def collect(events):
    return [e async for e in events]


def test_pause_edit_resume_persistence_and_clear(tmp_path):
    async def scenario():
        runtime = await make_runtime(tmp_path, GoalModel([]))
        thread = runtime.thread_id
        try:
            await runtime.goal_command("create", objective="old")
            await runtime.goal_command("pause")
            await runtime.goal_command("edit", objective="new")
            assert (await runtime.goal_command())["goal"]["status"] == "paused"
            assert await collect(runtime.continue_goal()) == []
        finally:
            await runtime.aclose()
        model = GoalModel([("update_goal", {"status": "complete"}), "done"])
        runtime = await make_runtime(tmp_path, model, thread=thread)
        try:
            assert (await runtime.goal_command())["goal"]["objective"] == "new"
            assert await collect(runtime.resume_pending()) == []
            await runtime.goal_command("resume")
            await collect(runtime.continue_goal())
            assert (await runtime.goal_command())["goal"]["status"] == "complete"
            await runtime.goal_command("clear")
            assert (await runtime.goal_command())["goal"] is None
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_plan_mode_does_not_pursue_or_charge_goal(tmp_path):
    async def scenario():
        runtime = await make_runtime(
            tmp_path, GoalModel(["plan"]), configured=settings(tmp_path, collaboration_mode="plan")
        )
        try:
            await runtime.goal_command("create", objective="implement")
            assert await collect(runtime.continue_goal()) == []
            await collect(runtime.stream("plan first"))
            assert (await runtime.goal_command())["goal"]["tokens_used"] == 0
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_model_creates_goal_and_existing_stream_continues(tmp_path):
    async def scenario():
        model = GoalModel(
            [
                ("create_goal", {"objective": "implement"}),
                "part one",
                ("update_goal", {"status": "complete"}),
                "done",
            ]
        )
        runtime = await make_runtime(tmp_path, model)
        try:
            events = await collect(runtime.stream("Create a goal to implement this"))
            assert sum(isinstance(e, TurnStarted) for e in events) == 2
            result = await runtime.goal_command()
            assert result["goal"]["status"] == "complete"
            assert result["goal"]["tokens_used"] == 80
            history = await runtime._repository.load_items(runtime.thread_id)
            assert sum(isinstance(item, UserMessageItem) for item in history) == 1
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_active_goal_resumes_after_reopen(tmp_path):
    async def scenario():
        runtime = await make_runtime(tmp_path, GoalModel([]))
        try:
            await runtime.goal_command("create", objective="persist")
            thread = runtime.thread_id
        finally:
            await runtime.aclose()
        model = GoalModel([("update_goal", {"status": "complete"}), "done"])
        runtime = await make_runtime(tmp_path, model, thread=thread)
        try:
            events = await collect(runtime.resume_pending())
            assert any(isinstance(e, TurnStarted) for e in events)
            assert (await runtime.goal_command())["goal"]["status"] == "complete"
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("control", ["pause", "clear", "cancel"])
def test_live_controls_prevent_another_automatic_turn(tmp_path, control):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        class Waiting(GoalModel):
            async def stream(self, request):
                entered.set()
                await release.wait()
                async for event in super().stream(request):
                    yield event

        model = Waiting(["partial"])
        runtime = await make_runtime(tmp_path, model)
        task = None
        try:
            await runtime.goal_command("create", objective="long task")
            task = asyncio.create_task(collect(runtime.continue_goal()))
            await asyncio.wait_for(entered.wait(), 5)
            # A second idle observer cannot enqueue a duplicate turn.
            assert await collect(runtime.continue_goal()) == []
            if control == "cancel":
                await runtime.cancel_active()
            else:
                await runtime.goal_command(control)
            release.set()
            if control == "cancel":
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert await collect(runtime.continue_goal()) == []
                assert (await runtime.goal_command())["goal"]["status"] == "paused"
            else:
                await asyncio.wait_for(task, 5)
                result = await runtime.goal_command()
                assert (
                    result["goal"] is None
                    if control == "clear"
                    else result["goal"]["status"] == "paused"
                )
            assert len(model.requests) <= 1
        finally:
            release.set()
            await runtime.aclose()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_budget_notice_reaches_next_model_step(tmp_path):
    async def scenario():
        model = GoalModel([("get_goal", {}), "budget summary"])
        runtime = await make_runtime(tmp_path, model)
        try:
            await runtime.goal_command("create", objective="work", token_budget=30)
            await collect(runtime.continue_goal())
            assert len(model.requests) == 2
            assert any(
                "do not start new substantive work" in getattr(i, "content", "")
                for i in model.requests[-1].items
            )
            assert (await runtime.goal_command())["goal"]["tokens_used"] == 80
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_goal_state_cannot_be_manipulated_with_model_resume(tmp_path):
    async def scenario():
        model = GoalModel([("update_goal", {"status": "active"}), "not allowed"])
        runtime = await make_runtime(tmp_path, model)
        try:
            await runtime.goal_command("create", objective="work")
            await runtime.goal_command("pause")
            await collect(runtime.stream("read the status"))
            assert (await runtime.goal_command())["goal"]["status"] == "paused"
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_disabled_goals_leave_ordinary_chat_unchanged(tmp_path):
    async def scenario():
        model = GoalModel(["hello"])
        runtime = await make_runtime(
            tmp_path, model, configured=settings(tmp_path, goals_enabled=False)
        )
        try:
            await collect(runtime.stream("hi"))
            assert not any(
                t.name in {"get_goal", "create_goal", "update_goal"}
                for t in model.requests[0].tools
            )
            assert not (tmp_path / "goals_1.sqlite").exists()
            with pytest.raises(ValueError):
                await runtime.goal_command("create", objective="disabled")
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_live_objective_edit_reaches_following_model_request(tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        class Hold:
            from corki.protocol.tools import ToolSpec

            spec = ToolSpec("hold", "hold", {"type": "object"})

            async def execute(self, call, context):
                from corki.protocol.tools import ToolResult

                entered.set()
                await release.wait()
                return ToolResult(call.id, call.name, "ready")

        from corki.tools.registry import ToolRegistry

        registry = ToolRegistry()
        registry.register(Hold())
        model = GoalModel([("hold", {}), ("update_goal", {"status": "complete"}), "done"])
        runtime = await make_runtime(tmp_path, model, registry=registry)
        task = None
        try:
            await runtime.goal_command("create", objective="old task")
            task = asyncio.create_task(collect(runtime.continue_goal()))
            await asyncio.wait_for(entered.wait(), 5)
            await runtime.goal_command("edit", objective="new task")
            release.set()
            await asyncio.wait_for(task, 5)
            assert any("new task" in getattr(i, "content", "") for i in model.requests[1].items)
            assert (await runtime.goal_command())["goal"]["objective"] == "new task"
        finally:
            release.set()
            await runtime.aclose()
            if task:
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["pause", "clear"])
def test_stopped_goal_before_worker_start_does_not_sample(tmp_path, action):
    async def scenario():
        from corki.sessions.models import TurnStatus

        runtime = await make_runtime(tmp_path, GoalModel([]))
        entered, release = asyncio.Event(), asyncio.Event()
        save = runtime._repository.save_turn

        async def save_with_barrier(turn):
            if turn.status == TurnStatus.RUNNING:
                entered.set()
                await release.wait()
            await save(turn)

        runtime._repository.save_turn = save_with_barrier
        task = None
        try:
            await runtime.goal_command("create", objective="work")
            task = asyncio.create_task(collect(runtime.continue_goal()))
            await asyncio.wait_for(entered.wait(), 5)
            await runtime.goal_command(action)
            release.set()
            events = await asyncio.wait_for(task, 5)
            assert isinstance(events[-1], TurnCompleted)
        finally:
            release.set()
            await runtime.aclose()
            if task:
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_plan_to_default_can_resume_goal_without_extra_user_turn(tmp_path):
    async def scenario():
        runtime = await make_runtime(
            tmp_path,
            GoalModel(["plan", ("update_goal", {"status": "complete"}), "done"]),
            configured=settings(tmp_path, collaboration_mode="plan"),
        )
        try:
            await runtime.goal_command("create", objective="implement")
            await collect(runtime.stream("plan"))
            from corki.protocol.collaboration import CollaborationMode

            await runtime.update_thread_settings(
                collaboration_mode=CollaborationMode("default", runtime.thread_settings.model, None)
            )
            await runtime.goal_command("resume")
            await collect(runtime.continue_goal())
            assert (await runtime.goal_command())["goal"]["status"] == "complete"
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("external", [False, True])
def test_runtime_fork_copies_goal_and_waits_for_new_input(tmp_path, external):
    async def scenario():
        from corki.core import LangGraphRuntime
        from corki.storage.sqlite import SQLiteSessionRepository

        source = await make_runtime(tmp_path, GoalModel([]))
        try:
            source_id = source.thread_id
            await source.goal_command("create", objective="fork goal", token_budget=500)
        finally:
            await source.aclose()
        database = tmp_path / "other" / "sessions.db" if external else tmp_path / "sessions.db"
        model = GoalModel([("update_goal", {"status": "complete"}), "done"])
        fork = await LangGraphRuntime.acreate(
            settings=settings(tmp_path),
            model=model,
            database_path=database,
            home_path=tmp_path / "home",
            fork_from_thread_id=source_id,
            fork_source_repository=SQLiteSessionRepository(tmp_path / "sessions.db")
            if external
            else None,
        )
        try:
            assert await collect(fork.resume_pending()) == []
            goal = (await fork.goal_command())["goal"]
            assert goal["objective"] == "fork goal" and goal["token_budget"] == 500
            await collect(fork.stream("continue the fork"))
            assert (await fork.goal_command())["goal"]["status"] == "complete"
            from corki.goals.store import GoalStore

            assert (await GoalStore(tmp_path / "goals_1.sqlite").get(source_id)).status == "active"
        finally:
            await fork.aclose()

    asyncio.run(scenario())


def test_ephemeral_goal_tools_reject_execution_without_disk_goal_state(tmp_path):
    async def scenario():
        from corki.core import LangGraphRuntime

        model = GoalModel([("create_goal", {"objective": "temporary"}), "cannot persist"])
        runtime = await LangGraphRuntime.acreate(
            settings=settings(tmp_path),
            model=model,
            database_path=tmp_path / "volatile" / "s.db",
            home_path=tmp_path / "home",
            ephemeral=True,
        )
        try:
            await collect(runtime.stream("create a goal"))
            assert any(t.name == "create_goal" for t in model.requests[0].tools)
            assert not (tmp_path / "volatile").exists()
            assert any("persistent" in getattr(i, "content", "") for i in model.requests[-1].items)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
