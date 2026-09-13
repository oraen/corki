"""Producer-owned diffs through real Runtime requests, compaction and SQLite history."""

import asyncio

import pytest

from corki.config import CorkiSettings
from corki.context.extension_world_state import (
    PreviousKind,
    WorldStateFragment,
    WorldStateSection,
)
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.models.types import ModelUsage
from corki.protocol.events import TurnCancelled, TurnCompleted, TurnFailed
from corki.protocol.items import AssistantMessageItem, ContextItem, ContextRole, new_step_id


@pytest.mark.parametrize("automatic", [False, True])
def test_extension_diff_silent_removal_and_cold_reattachment(tmp_path, automatic):
    async def scenario():
        seen, requests = [], []

        class Source:
            revision = 1
            enabled = True
            silent = False

            async def world_state_contributions(self, **kwargs):
                revision, silent = self.revision, self.silent

                def render(previous):
                    seen.append((previous.kind, previous.snapshot_json))
                    if silent or previous.snapshot_json == str(revision):
                        return None
                    return WorldStateFragment(ContextRole.DEVELOPER, f"CUSTOM_DELTA_{revision}")

                return (
                    (WorldStateSection("fixture", str(revision), render),) if self.enabled else ()
                )

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),),
                    ModelUsage(180_000 if automatic and len(requests) == 8 else 10, 10),
                )

            async def aclose(self):
                pass

        source = Source()

        async def create(thread=None):
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    working_directory=tmp_path,
                    skills_enabled=False,
                    plugins_enabled=False,
                    memories_enabled=False,
                    context_window_tokens=200_000,
                    auto_compact_tokens=100_000,
                ),
                database_path=tmp_path / "history.db",
                home_path=tmp_path / "home",
                model=Model(),
                context_contributors=(source,),
                thread_id=thread,
            )

        async def turn(runtime):
            before = await runtime._repository.load_items(runtime.thread_id)
            assert isinstance([e async for e in runtime.stream("continue")][-1], TurnCompleted)
            after = await runtime._repository.load_items(runtime.thread_id)
            assert after[: len(before)] == before
            return [
                row
                for row in after[len(before) :]
                if isinstance(row, ContextItem) and row.key == "extension.world_state.fixture"
            ]

        runtime = await create()
        try:
            assert [row.content for row in await turn(runtime)] == ["CUSTOM_DELTA_1"]
            assert seen[-1] == (PreviousKind.ABSENT, None)
            assert await turn(runtime) == []
            assert seen[-1] == (PreviousKind.KNOWN, "1")
            source.revision = 2
            assert [row.content for row in await turn(runtime)] == ["CUSTOM_DELTA_2"]
            source.silent, source.revision = True, 3
            silent = await turn(runtime)
            assert len(silent) == 1 and silent[0].is_snapshot_only
            thread = runtime.thread_id
        finally:
            await runtime.aclose()

        runtime = await create(thread)
        try:
            assert await turn(runtime) == []
            assert seen[-1] == (PreviousKind.KNOWN, "3")
            source.enabled = False
            removed = await turn(runtime)
            assert len(removed) == 1 and removed[0].is_snapshot_only
            assert await turn(runtime) == []
        finally:
            await runtime.aclose()

        source.enabled, source.silent = True, False
        runtime = await create(thread)
        try:
            assert [row.content for row in await turn(runtime)] == ["CUSTOM_DELTA_3"]
            assert seen[-1] == (PreviousKind.ABSENT, None)
            visible = [
                row.content
                for row in requests[-1].items
                if isinstance(row, ContextItem) and row.key == "extension.world_state.fixture"
            ]
            assert visible == ["CUSTOM_DELTA_1", "CUSTOM_DELTA_2", "CUSTOM_DELTA_3"]
            if not automatic:
                compact_events = [event async for event in runtime.compact()]
                assert any(type(event).__name__ == "ContextCompacted" for event in compact_events)
            assert [row.content for row in await turn(runtime)] == ["CUSTOM_DELTA_3"]
            assert seen[-1] == (PreviousKind.ABSENT, None)
            visible = [
                row.content
                for row in requests[-1].items
                if isinstance(row, ContextItem) and row.key == "extension.world_state.fixture"
            ]
            assert visible == ["CUSTOM_DELTA_3"]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault",
    ["duplicate", "invalid_result", "invalid_role", "exception", "cancel_capture", "cancel_render"],
)
def test_extension_fault_fails_before_model_or_partial_context_commit(tmp_path, fault):
    async def scenario():
        requests = []

        class Source:
            async def world_state_contributions(self, **kwargs):
                if fault == "cancel_capture":
                    raise asyncio.CancelledError("fixture capture cancelled")

                def render(previous):
                    if fault == "cancel_render":
                        raise asyncio.CancelledError("fixture renderer cancelled")
                    if fault == "invalid_result":
                        return "not a fragment"
                    if fault == "invalid_role":
                        return WorldStateFragment("system", "INVALID")
                    if fault == "exception":
                        raise RuntimeError("fixture renderer failure")
                    return WorldStateFragment(ContextRole.DEVELOPER, "VALID")

                section = WorldStateSection("fault", "1", render)
                return (section, section) if fault == "duplicate" else (section,)

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                plugins_enabled=False,
                memories_enabled=False,
            ),
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
            model=Model(),
            context_contributors=(Source(),),
        )
        try:
            events = []
            if fault.startswith("cancel_"):
                with pytest.raises(asyncio.CancelledError):
                    async for event in runtime.stream("run"):
                        events.append(event)
                assert isinstance(events[-1], TurnCancelled)
                assert sum(isinstance(event, TurnCancelled) for event in events) == 1
                assert not any(isinstance(event, TurnFailed) for event in events)
            else:
                events = [event async for event in runtime.stream("run")]
                assert isinstance(events[-1], TurnFailed)
                assert sum(isinstance(event, TurnFailed) for event in events) == 1
            assert not any(isinstance(event, TurnCompleted) for event in events)
            assert requests == []
            history = await runtime._repository.load_items(runtime.thread_id)
            assert not any(
                isinstance(row, ContextItem) and row.key == "extension.world_state.fault"
                for row in history
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
