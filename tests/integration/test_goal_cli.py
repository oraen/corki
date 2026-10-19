"""Slash-command editing against real goal storage, without live model requests."""

import asyncio
from types import SimpleNamespace

import pytest
from test_goal_runtime import GoalModel, collect
from test_thread_settings_update import make_runtime

from corki.cli.application import CorkiApplication


def editor(runtime, answers):
    answers = iter(answers)
    notices = []

    async def read():
        return next(answers)

    app = object.__new__(CorkiApplication)
    app._runtime = runtime
    app._ui = SimpleNamespace(show_notice=notices.append)
    app._input = SimpleNamespace(read_message=read)
    app._realtime_enabled = False
    app._consume_events = collect
    return app, notices


def test_editor_updates_budget_preserves_pause_and_objective(tmp_path):
    async def scenario():
        runtime = await make_runtime(tmp_path, GoalModel([]))
        try:
            await runtime.goal_command("create", objective="original")
            await runtime.goal_command("pause")
            app, notices = editor(runtime, ["", "500"])
            await app._handle_goal("edit")
            goal = (await runtime.goal_command())["goal"]
            assert goal["objective"] == "original"
            assert goal["token_budget"] == 500
            assert goal["status"] == "paused"
            assert any("paused" in text for text in notices)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("answer", ["yes", "no"])
def test_replacement_requires_confirmation(tmp_path, answer):
    async def scenario():
        runtime = await make_runtime(
            tmp_path,
            GoalModel(
                [
                    ("update_goal", {"status": "complete"}),
                    "done",
                ]
            ),
        )
        try:
            await runtime.goal_command("create", objective="original")
            app, _ = editor(runtime, [answer])
            await app._handle_goal("replacement")
            goal = (await runtime.goal_command())["goal"]
            assert goal["objective"] == ("replacement" if answer == "yes" else "original")
            assert goal["status"] == ("complete" if answer == "yes" else "active")
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_invalid_editor_budget_does_not_partially_save_objective(tmp_path):
    async def scenario():
        runtime = await make_runtime(tmp_path, GoalModel([]))
        try:
            await runtime.goal_command("create", objective="original")
            app, notices = editor(runtime, ["changed", "0"])
            await app._handle_goal("edit")
            assert (await runtime.goal_command())["goal"]["objective"] == "original"
            assert any("positive integer" in text for text in notices)
            await app._handle_goal("")  # viewing never starts the goal
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
