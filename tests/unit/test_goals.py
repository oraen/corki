"""Goal state invariants, accounting, cancellation and Codex parity edges."""

import asyncio
from unittest.mock import patch

import pytest

from corki.goals.runtime import GoalController
from corki.goals.store import GoalStore, validate_budget, validate_objective
from corki.protocol.events import TokenUsageUpdated, ToolCallCompleted, TurnCompleted


@pytest.mark.parametrize("value", ["", "  ", None, "x" * 4001])
def test_reject_invalid_objective(value):
    with pytest.raises(ValueError):
        validate_objective(value)


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "100", 2**63])
def test_reject_invalid_budget(value):
    with pytest.raises(ValueError):
        validate_budget(value)


def test_store_isolation_atomic_dedup_and_stale_usage(tmp_path):
    async def scenario():
        store = GoalStore(tmp_path / "goals.db")
        first = await store.create("thread-a", "first", 100)
        await store.create("thread-b", "other")
        with pytest.raises(ValueError):
            await store.create("thread-a", "replacement")
        await asyncio.gather(
            *(
                store.account("thread-a", first.goal_id, tokens=10, usage_id=f"step-{n}")
                for n in range(10)
            )
        )
        goal = await store.get("thread-a")
        assert goal.tokens_used == 100 and goal.status == "budget_limited"
        await store.account("thread-a", first.goal_id, tokens=10, usage_id="step-1")
        assert (await store.get("thread-a")).tokens_used == 100
        assert (await store.get("thread-b")).tokens_used == 0
        await store.update("thread-a", status="complete")
        new = await store.create("thread-a", "replacement")
        await store.account("thread-a", first.goal_id, tokens=500)
        assert (await store.get("thread-a")) == new

    asyncio.run(scenario())


def test_model_creation_budget_cap_and_failed_authorization_record(tmp_path):
    async def scenario():
        controller = GoalController(tmp_path / "goals.db", "a", max_token_budget=100)
        with pytest.raises(ValueError):
            await controller.create("goal", 101)

        async def failed_record():
            raise OSError("history unavailable")

        with pytest.raises(OSError):
            await controller.create("goal", record=failed_record)
        assert (await controller.get())["goal"] is None
        result = await controller.create("goal")
        assert result["goal"]["token_budget"] == 100
        with pytest.raises(ValueError):
            await controller.update(token_budget=101)

    asyncio.run(scenario())


def test_elapsed_accounting_and_completion_flush(tmp_path):
    async def scenario():
        controller = GoalController(tmp_path / "goals.db", "a")
        with patch("corki.goals.runtime.time.monotonic", return_value=10):
            await controller.create("goal")
            await controller.begin("turn")
        with patch("corki.goals.runtime.time.monotonic", return_value=15.9):
            await controller.observe(
                TokenUsageUpdated("a", "turn", 100, 20, 80, 10, usage_id="step")
            )
            result = await controller.update(status="complete")
        assert result["goal"]["tokens_used"] == 40
        assert result["goal"]["time_used_seconds"] == 5
        assert result["completion_budget_report"]

    asyncio.run(scenario())


def test_root_goal_counts_descendants_once(tmp_path):
    async def scenario():
        root = GoalController(tmp_path / "goals.db", "root")
        child = GoalController(tmp_path / "goals.db", "child", parent_thread_id="root")
        grandchild = GoalController(tmp_path / "goals.db", "grandchild", parent_thread_id="child")
        root.register()
        child.register()
        grandchild.register()
        try:
            await root.create("goal")
            await root.begin("root-turn")
            event = TokenUsageUpdated("grandchild", "t", 100, 20, 80, 0, usage_id="step")
            await grandchild.observe(event)
            await grandchild.observe(event)
            assert (await root.get())["goal"]["tokens_used"] == 40
            assert (await child.get())["goal"] is None
        finally:
            root.unregister()
            child.unregister()
            grandchild.unregister()

    asyncio.run(scenario())


def test_fork_snapshot_defers_until_user_turn(tmp_path):
    async def scenario():
        store = GoalStore(tmp_path / "goals.db")
        goal = await store.create("parent", "goal", 100)
        await store.account("parent", goal.goal_id, tokens=10)
        await store.copy_snapshot("fork", await store.get("parent"))
        controller = GoalController(store.path, "fork")
        await controller.load()
        assert not controller.can_continue
        assert controller.goal.tokens_used == 10
        await controller.begin("turn")
        assert controller.can_continue

    asyncio.run(scenario())


def test_only_repeated_handler_exec_failures_block(tmp_path):
    async def scenario():
        controller = GoalController(tmp_path / "goals.db", "a")
        await controller.create("goal")
        for n in range(3):
            await controller.begin(str(n), automatic=True)
            # Ordinary nonzero commands are observations, not infrastructure failures.
            await controller.observe(ToolCallCompleted("a", str(n), "c", "exec", True))
            await controller.finish(TurnCompleted("a", str(n), "trying"))
        assert controller.goal.status == "active"
        for n in range(3):
            await controller.begin(str(n), automatic=True)
            await controller.observe(
                ToolCallCompleted("a", str(n), "c", "exec", True, handler_failed=True)
            )
            await controller.finish(TurnCompleted("a", str(n), "execution unavailable"))
        assert controller.goal.status == "blocked"
        await controller.update(status="active")
        assert controller.failure_turns == 0

    asyncio.run(scenario())


def test_tool_wire_shape_matches_codex_without_internal_identifiers():
    from corki.goals.store import Goal
    from corki.goals.tools import codex_response

    result = Goal(
        "thread", "internal-id", "goal", status="budget_limited", token_budget=10, tokens_used=12
    ).response()
    wire = codex_response(result)
    assert wire["goal"]["status"] == "budgetLimited"
    assert wire["goal"]["tokensUsed"] == 12
    assert wire["remainingTokens"] == 0
    assert "goal_id" not in wire["goal"] and "goalId" not in wire["goal"]
    wire = codex_response(Goal("thread", "internal", "goal").response())
    assert "tokenBudget" not in wire["goal"]
    assert wire["completionBudgetReport"] is None


def test_clearing_fork_does_not_erase_source_usage_receipts(tmp_path):
    async def scenario():
        store = GoalStore(tmp_path / "goals.db")
        source = await store.create("source", "goal")
        await store.account("source", source.goal_id, tokens=40, usage_id="step")
        await store.copy_snapshot("fork", await store.get("source"))
        await store.clear("fork")
        await store.account("source", source.goal_id, tokens=40, usage_id="step")
        assert (await store.get("source")).tokens_used == 40

    asyncio.run(scenario())


def test_short_turns_accumulate_elapsed_fraction(tmp_path):
    async def scenario():
        controller = GoalController(tmp_path / "goals.db", "thread")
        await controller.create("goal")
        for n in range(3):
            with patch("corki.goals.runtime.time.monotonic", return_value=10 + n):
                await controller.begin(str(n), automatic=True)
            with patch("corki.goals.runtime.time.monotonic", return_value=10 + n + 0.4):
                await controller.finish(TurnCompleted("thread", str(n), "progress"))
        assert (await controller.get())["goal"]["time_used_seconds"] == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("value", [0, -1, True, "100", [], 2**63])
def test_goal_budget_configuration_rejects_invalid_values(tmp_path, value):
    import json

    from corki.config import CorkiSettings

    config = tmp_path / "settings.json"
    config.write_text(json.dumps({"goals": {"max_goal_token_budget": value}}))
    with pytest.raises(ValueError):
        CorkiSettings.for_directory(tmp_path, config_file=config)


def test_goal_settings_load_and_default_behavior(tmp_path):
    from corki.config import CorkiSettings

    config = tmp_path / "settings.json"
    config.write_text('{"features":{"goals":false},"goals":{"max_goal_token_budget":1000}}')
    settings = CorkiSettings.for_directory(tmp_path, config_file=config)
    assert settings.goals_enabled is False
    assert settings.max_goal_token_budget == 1000
    assert CorkiSettings(tmp_path).goals_enabled is True
    assert CorkiSettings(tmp_path).max_goal_token_budget is None
