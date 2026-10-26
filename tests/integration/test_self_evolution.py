"""Actual foreground + ephemeral review runtimes, with an in-process fake model."""

import asyncio
import json
import sqlite3
from dataclasses import replace

from corki.config import CorkiSettings
from corki.core.runtime import LangGraphRuntime
from corki.evolution.state import EvolutionState
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.items import AssistantMessageItem, ToolCallItem, new_step_id
from corki.protocol.tools import ToolCall

DOCUMENT = (
    "---\nname: learned-checks\ndescription: Verify results before delivery.\n---\nCheck results.\n"
)


def settings(home, work, enabled):
    home.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    path = home / "settings.json"
    path.write_text(
        json.dumps({"features": {"self_evolution": enabled}, "memories": {"enabled": False}})
    )
    return CorkiSettings.for_directory(work, config_file=path)


class Model:
    def __init__(self):
        self.foreground = []
        self.reviews = []
        self.review_started = asyncio.Event()
        self.review_cancelled = asyncio.Event()
        self.review_release = asyncio.Event()
        self.stall = False

    async def stream(self, request):
        turn, step = request.items[-1].turn_id, new_step_id()
        if "bounded, background procedural-learning reviewer" in request.instructions:
            self.reviews.append(request)
            assert {t.name for t in request.tools} == {"skill_manage"}
            self.review_started.set()
            if self.stall:
                try:
                    await self.review_release.wait()
                except asyncio.CancelledError:
                    self.review_cancelled.set()
                    raise
            if len(self.reviews) == 1:
                yield ModelCompleted(
                    (
                        ToolCallItem(
                            ToolCall(
                                "learn",
                                "skill_manage",
                                {
                                    "action": "create",
                                    "name": "learned-checks",
                                    "content": DOCUMENT,
                                },
                            ),
                            turn,
                            step,
                        ),
                    )
                )
            else:
                yield ModelCompleted((AssistantMessageItem("Saved verified checks", turn, step),))
        else:
            self.foreground.append(request)
            yield ModelCompleted((AssistantMessageItem("Done", turn, step),))

    async def aclose(self):
        pass


async def turn(runtime, content="Please do the task"):
    events = [event async for event in runtime.stream(content)]
    assert isinstance(events[-1], TurnCompleted)
    return events


async def create(home, work, enabled=True):
    model = Model()
    runtime = await LangGraphRuntime.acreate(
        settings=replace(settings(home, work, enabled), hooks_enabled=False, goals_enabled=False),
        home_path=home,
        compatibility_home=home / "user",
        model=model,
        database_path=home / "sessions/corki.db",
        load_plugins=False,
    )
    return runtime, model


def test_feature_off_has_no_learning_surface_or_background_io(tmp_path):
    async def check():
        home, work = tmp_path / "home", tmp_path / "work"
        runtime, model = await create(home, work, False)
        try:
            for _ in range(11):
                await turn(runtime)
            assert not model.reviews and not (home / "evolution").exists()
            assert all("skill_manage" not in {t.name for t in r.tools} for r in model.foreground)
            assert all("<corki_self_evolution>" not in r.instructions for r in model.foreground)
        finally:
            await runtime.aclose()

    asyncio.run(check())


def test_enabled_reviews_persist_skills_without_memory_or_extra_history(tmp_path):
    async def check():
        home, work = tmp_path / "home", tmp_path / "work"
        runtime, model = await create(home, work)
        try:
            for _ in range(10):
                await turn(runtime)
            await asyncio.wait_for(runtime._evolution.task, 10)
            assert len(model.reviews) == 2
            assert (home / "skills/learned-checks/SKILL.md").read_text() == DOCUMENT
            assert all("skill_manage" in {t.name for t in r.tools} for r in model.foreground)
            state = EvolutionState(home).status()
            assert state["edits"] == 1 and state["reviews"][0]["status"] == "completed"
            assert not runtime._settings.memories_enabled
            with sqlite3.connect(home / "sessions/corki.db") as db:
                assert db.execute("SELECT COUNT(*) FROM threads").fetchone()[0] == 1
                assert db.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 10
            # The same live runtime respects the homepage switch on its next turn.
            config = json.loads((home / "settings.json").read_text())
            config["features"]["self_evolution"] = False
            (home / "settings.json").write_text(json.dumps(config))
            await turn(runtime)
            assert "skill_manage" not in {t.name for t in model.foreground[-1].tools}
            assert "<corki_self_evolution>" not in model.foreground[-1].instructions
            assert "learned-checks" in str(model.foreground[-1])
            assert (home / "skills/learned-checks/SKILL.md").exists()
        finally:
            await runtime.aclose()

    asyncio.run(check())


def test_new_foreground_turn_preempts_review_and_closes_owned_worker(tmp_path):
    async def check():
        home, work = tmp_path / "home", tmp_path / "work"
        runtime, model = await create(home, work)
        model.stall = True
        try:
            for _ in range(10):
                await turn(runtime)
            await asyncio.wait_for(model.review_started.wait(), 5)
            await asyncio.wait_for(turn(runtime, "New foreground request"), 5)
            assert model.review_cancelled.is_set()
            assert not (home / "skills/learned-checks/SKILL.md").exists()
            state = EvolutionState(home).status()
            assert state["reviews"][0]["status"] == "cancelled"
        finally:
            await runtime.aclose()

    asyncio.run(check())


def test_disabling_feature_applies_next_turn_without_interrupting_admitted_review(tmp_path):
    async def check():
        home, work = tmp_path / "home", tmp_path / "work"
        runtime, model = await create(home, work)
        model.stall = True
        try:
            for _ in range(10):
                await turn(runtime)
            await asyncio.wait_for(model.review_started.wait(), 5)
            path = home / "settings.json"
            config = json.loads(path.read_text())
            config["features"]["self_evolution"] = False
            path.write_text(json.dumps(config))
            model.review_release.set()
            await asyncio.wait_for(runtime._evolution.task, 10)
            assert not model.review_cancelled.is_set()
            assert EvolutionState(home).status()["reviews"][0]["status"] == "completed"
            assert (home / "skills/learned-checks/SKILL.md").read_text() == DOCUMENT
            await turn(runtime)
            assert "skill_manage" not in {t.name for t in model.foreground[-1].tools}
            assert len(model.reviews) == 2
        finally:
            await runtime.aclose()

    asyncio.run(check())


def test_homepage_flag_can_enable_an_already_running_agent_without_memory(tmp_path):
    async def check():
        home, work = tmp_path / "home", tmp_path / "work"
        runtime, model = await create(home, work, False)
        try:
            await turn(runtime)
            assert "skill_manage" not in {t.name for t in model.foreground[-1].tools}
            path = home / "settings.json"
            config = json.loads(path.read_text())
            config["features"]["self_evolution"] = True
            path.write_text(json.dumps(config))
            await turn(runtime)
            request = model.foreground[-1]
            assert "skill_manage" in {t.name for t in request.tools}
            assert "<corki_self_evolution>" in str(request)
            assert not runtime._settings.memories_enabled
        finally:
            await runtime.aclose()

    asyncio.run(check())


def test_review_budget_does_not_allow_an_infinite_skill_tool_loop(tmp_path):
    class LoopModel(Model):
        async def stream(self, request):
            if "bounded, background procedural-learning reviewer" not in request.instructions:
                async for event in super().stream(request):
                    yield event
                return
            self.reviews.append(request)
            turn_id, step = request.items[-1].turn_id, new_step_id()
            yield ModelCompleted(
                (
                    ToolCallItem(
                        ToolCall(
                            "loop-" + str(len(self.reviews)), "skill_manage", {"action": "list"}
                        ),
                        turn_id,
                        step,
                    ),
                )
            )

    async def check():
        home, work = tmp_path / "home", tmp_path / "work"
        model = LoopModel()
        runtime = await LangGraphRuntime.acreate(
            settings=replace(settings(home, work, True), goals_enabled=False, hooks_enabled=False),
            home_path=home,
            compatibility_home=home / "user",
            model=model,
            database_path=home / "sessions/corki.db",
            load_plugins=False,
        )
        try:
            for _ in range(10):
                await turn(runtime)
            await asyncio.wait_for(runtime._evolution.task, 10)
            assert 1 < len(model.reviews) <= 16  # The aggregate input budget may bind first.
            assert EvolutionState(home).status()["reviews"][0]["status"] == "failed"
        finally:
            await runtime.aclose()

    asyncio.run(check())


def test_foreground_in_another_session_preempts_review_and_close_releases_lease(tmp_path):
    from corki.storage.file_lock import open_lock, try_lock, unlock_and_close

    async def check():
        home, work = tmp_path / "home", tmp_path / "work"
        first, model = await create(home, work)
        second, _ = await create(home, work)
        model.stall = True
        try:
            for _ in range(10):
                await turn(first)
            await asyncio.wait_for(model.review_started.wait(), 5)
            await asyncio.wait_for(turn(second), 5)
            assert model.review_cancelled.is_set()
            assert EvolutionState(home).status()["reviews"][0]["status"] == "cancelled"
        finally:
            await first.aclose()
            await second.aclose()
        owner = open_lock(home / "evolution/review.lock")
        try:
            assert try_lock(owner)
        finally:
            unlock_and_close(owner)

    asyncio.run(check())
