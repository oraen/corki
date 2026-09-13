"""Host provider selection and snapshot ownership through actual Runtime admission."""

import asyncio
import threading
from contextlib import suppress

import pytest

from corki.config import CorkiSettings
from corki.context.user_instructions import (
    HomeUserInstructionsProvider,
    Instructions,
    LoadedUserInstructions,
)
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.items import AssistantMessageItem, ContextItem, new_step_id
from corki.protocol.session_source import SessionSource, SubAgentSource


class RecordingModel:
    def __init__(self):
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        yield ModelCompleted(
            (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
        )

    async def aclose(self):
        pass


def create(tmp_path, model, **kwargs):
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    return LangGraphRuntime.create(
        settings=CorkiSettings(working_directory=project, skills_enabled=False),
        home_path=tmp_path / "home",
        database_path=tmp_path / "s.db",
        model=model,
        **kwargs,
    )


def test_explicit_falsey_provider_is_loaded_once_and_keeps_provenance(tmp_path):
    async def scenario():
        class Provider:
            calls = 0

            def __bool__(self):
                return False

            async def load_user_instructions(self):
                self.calls += 1
                return LoadedUserInstructions(Instructions("HOST RULE", tmp_path / "host.md"))

        provider, model = Provider(), RecordingModel()
        runtime = create(tmp_path, model, user_instructions_provider=provider)
        try:
            for _ in range(2):
                assert isinstance([e async for e in runtime.stream("go")][-1], TurnCompleted)
            assert provider.calls == 1
            assert await runtime.instruction_sources() == (tmp_path / "host.md",)
            assert await runtime.user_instructions() == Instructions(
                "HOST RULE", tmp_path / "host.md"
            )
            agents = [
                i
                for i in model.requests[-1].items
                if isinstance(i, ContextItem) and i.key == "project.agents"
            ]
            assert len(agents) == 1 and "HOST RULE" in agents[0].content
            assert "instructions for " not in agents[0].content
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "source",
    [
        SessionSource.internal("guardian"),
        SessionSource.subagent(SubAgentSource("other", "guardian")),
    ],
)
def test_guardian_does_not_inherit_global_instructions(tmp_path, source):
    async def scenario():
        model = RecordingModel()

        class ForbiddenProvider:
            async def load_user_instructions(self):
                raise AssertionError("internal host must not reload global files")

        runtime = create(
            tmp_path,
            model,
            session_source=source,
            user_instructions_provider=ForbiddenProvider(),
            inherited_user_instructions=Instructions("PARENT RULE", tmp_path / "parent.md"),
        )
        try:
            assert isinstance([e async for e in runtime.stream("go")][-1], TurnCompleted)
            assert await runtime.user_instructions() is None
            assert await runtime.instruction_sources() == ()
            assert not any(
                isinstance(i, ContextItem) and "PARENT RULE" in i.content
                for i in model.requests[0].items
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_failed_startup_reloads_unpublished_instruction_snapshot(tmp_path, monkeypatch):
    async def scenario():
        model = RecordingModel()
        runtime = create(tmp_path, model)
        home = tmp_path / "home"
        home.mkdir(exist_ok=True)
        rule = home / "AGENTS.md"
        rule.write_text("PROVISIONAL RULE")
        ensure_thread = runtime._ensure_thread

        async def fail_after_instructions():
            raise OSError("thread admission failed")

        monkeypatch.setattr(runtime, "_ensure_thread", fail_after_instructions)
        try:
            with pytest.raises(OSError, match="thread admission failed"):
                await runtime.instruction_sources()
            assert not model.requests
            rule.write_text("RETRIED RULE")
            monkeypatch.setattr(runtime, "_ensure_thread", ensure_thread)
            assert isinstance([e async for e in runtime.stream("go")][-1], TurnCompleted)
            assert (await runtime.user_instructions()).text == "RETRIED RULE"
            assert not any(
                isinstance(i, ContextItem) and "PROVISIONAL RULE" in i.content
                for i in model.requests[0].items
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["cancel", "repeat_cancel", "close"])
def test_startup_owns_global_file_reader_until_joined(tmp_path, action):
    async def scenario():
        reached, release, joined = asyncio.Event(), threading.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        home = tmp_path / "home"
        home.mkdir()
        rule = home / "AGENTS.md"
        rule.write_text("PROVISIONAL RULE")

        class HeldProvider(HomeUserInstructionsProvider):
            calls = 0

            def _load(self):
                self.calls += 1
                result = super()._load()
                if self.calls == 1:
                    loop.call_soon_threadsafe(reached.set)
                    try:
                        assert release.wait(5)
                    finally:
                        joined.set()
                return result

        provider, model = HeldProvider(home), RecordingModel()
        runtime = create(tmp_path, model, user_instructions_provider=provider)
        startup = asyncio.create_task(runtime.instruction_sources())
        closing = None
        try:
            await asyncio.wait_for(reached.wait(), 3)
            if action == "close":
                closing = asyncio.create_task(runtime.aclose())
            else:
                startup.cancel()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            if action == "repeat_cancel":
                startup.cancel()
            assert not startup.done()
            assert not joined.is_set()
            assert not model.requests
            if closing is not None:
                assert not closing.done()
            rule.write_text("RETRIED RULE")
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await startup
            assert joined.is_set()
            if closing is not None:
                await closing
                assert not model.requests
            else:
                assert isinstance([e async for e in runtime.stream("retry")][-1], TurnCompleted)
                assert provider.calls == 2
                assert (await runtime.user_instructions()).text == "RETRIED RULE"
        finally:
            release.set()
            with suppress(asyncio.CancelledError):
                await startup
            if closing is not None:
                await closing
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("inherited", [False, True])
def test_non_root_uses_only_host_inherited_global_snapshot(tmp_path, inherited):
    async def scenario():
        class ForbiddenProvider:
            async def load_user_instructions(self):
                raise AssertionError("non-root must not load the host provider")

        model = RecordingModel()
        snapshot = Instructions("PARENT SNAPSHOT", tmp_path / "parent.md") if inherited else None
        runtime = create(
            tmp_path,
            model,
            session_source=SessionSource.internal("memory_consolidation"),
            inherited_user_instructions=snapshot,
            user_instructions_provider=ForbiddenProvider(),
        )
        (tmp_path / "project/AGENTS.md").write_text("CHILD PROJECT")
        try:
            assert isinstance([e async for e in runtime.stream("go")][-1], TurnCompleted)
            assert await runtime.user_instructions() == snapshot
            sources = ((snapshot.source,) if snapshot else ()) + (tmp_path / "project/AGENTS.md",)
            assert await runtime.instruction_sources() == sources
            agents = [
                i
                for i in model.requests[0].items
                if isinstance(i, ContextItem) and i.key == "project.agents"
            ]
            assert len(agents) == 1 and "CHILD PROJECT" in agents[0].content
            assert ("PARENT SNAPSHOT" in agents[0].content) == inherited
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
