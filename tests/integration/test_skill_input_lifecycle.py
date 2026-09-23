import asyncio

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    ContextItem,
    ToolCallItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.skills import SkillService
from corki.tools import ToolRegistry


def write_skill(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\nname: fixture\ndescription: fixture skill\n---\n\n" + body, encoding="utf-8"
    )


def selected(items):
    return [
        i
        for i in items
        if isinstance(i, ContextItem) and i.key.startswith("extensions.skills.selected.")
    ]


@pytest.mark.parametrize("change", ["edit", "delete"])
def test_explicit_skill_is_frozen_input_history_not_a_step_snapshot(tmp_path, monkeypatch, change):
    async def scenario():
        path = tmp_path / ".corki" / "skills" / "fixture" / "SKILL.md"
        write_skill(path, "ORIGINAL SKILL BODY")
        requests, reads = [], []
        original_read = SkillService.read

        def read(service, skill, relative_file="SKILL.md"):
            if skill.name == "fixture":
                reads.append(skill.path)
            return original_read(service, skill, relative_file)

        monkeypatch.setattr(SkillService, "read", read)

        class Change:
            spec = ToolSpec("change", "change the skill file", {})

            async def execute(self, call, context):
                if change == "edit":
                    write_skill(path, "UPDATED SKILL BODY")
                else:
                    path.unlink()
                return ToolResult(call.id, call.name, "changed")

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    items = (ToolCallItem(ToolCall(new_tool_call_id(), "change", {}), turn, step),)
                else:
                    items = (AssistantMessageItem("done", turn, step),)
                yield ModelCompleted(items)

            async def aclose(self):
                pass

        async def create(thread_id=None):
            registry = ToolRegistry()
            registry.register(Change())
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(working_directory=tmp_path),
                database_path=tmp_path / "sessions.db",
                home_path=tmp_path / ".corki",
                registry=registry,
                model=Model(),
                thread_id=thread_id,
            )

        runtime = await create()
        try:
            events = [e async for e in runtime.stream("use $fixture")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(reads) == 1
            initial = selected(requests[0].items)
            assert len(initial) == 1 and "ORIGINAL SKILL BODY" in initial[0].content
            user_index = next(
                i for i, x in enumerate(requests[0].items) if isinstance(x, UserMessageItem)
            )
            assert requests[0].items[user_index + 1 :] == tuple(initial)
            assert selected(requests[1].items) == initial
            events = [e async for e in runtime.stream("continue without another mention")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert selected(requests[-1].items) == initial
            assert len(reads) == 1
            thread = runtime.thread_id
            await runtime.aclose()
            write_skill(path, "UPDATED SKILL BODY")
            runtime = await create(thread)
            for _ in range(2):
                events = [e async for e in runtime.stream("use $fixture again")]
                assert isinstance(events[-1], TurnCompleted), events[-1]
            all_selected = selected(requests[-1].items)
            assert len(reads) == 3 and len(all_selected) == 3
            assert all_selected[0] == initial[0]
            assert all("UPDATED SKILL BODY" in i.content for i in all_selected[1:])
            assert all("no longer apply" not in i.content for i in all_selected)
            assert len({i.source_input_id for i in all_selected}) == 3
            stored = await runtime._repository.load_items(thread)
            assert selected(stored) == all_selected
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("include_catalog", [False, True])
def test_midturn_compaction_does_not_reinject_old_selected_skill(tmp_path, include_catalog):
    async def scenario():
        path = tmp_path / ".corki" / "skills" / "fixture" / "SKILL.md"
        write_skill(path, "SELECTED SKILL BODY")
        requests, summaries = [], []

        class Large:
            spec = ToolSpec("large", "large observation", {}, output_char_budget=100000)

            async def execute(self, call, context):
                return ToolResult(call.id, call.name, "OBSERVATION " + "x" * 90000)

        class Model:
            async def stream(self, request):
                turn, step = request.items[-1].turn_id, new_step_id()
                if (
                    isinstance(request.items[-1], UserMessageItem)
                    and "checkpoint compaction" in request.items[-1].content
                ):
                    summaries.append(request)
                    yield ModelCompleted(
                        (AssistantMessageItem("OBSERVATION retained", turn, step),)
                    )
                    return
                requests.append(request)
                if len(requests) == 1:
                    items = (ToolCallItem(ToolCall(new_tool_call_id(), "large", {}), turn, step),)
                else:
                    items = (AssistantMessageItem("done", turn, step),)
                yield ModelCompleted(items)

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Large())
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                context_window_tokens=50000,
                auto_compact_tokens=12000,
                tool_output_token_limit=25000,
                skills_include_instructions=include_catalog,
            ),
            database_path=tmp_path / "sessions.db",
            home_path=tmp_path / ".corki",
            registry=registry,
            model=Model(),
        )
        try:
            events = [e async for e in runtime.stream("use $fixture and obtain observation")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(summaries) == 1
            assert len(selected(summaries[0].items)) == 1
            assert not selected(requests[-1].items)
            assert any(isinstance(i, CompactionItem) for i in requests[-1].items)
            assert any(
                isinstance(i, UserMessageItem)
                and i.content == "use $fixture and obtain observation"
                for i in requests[-1].items
            )
            stored = await runtime._repository.load_items(runtime.thread_id)
            assert len(selected(stored)) == 1
            if not include_catalog:
                assert not any(
                    isinstance(i, ContextItem) and i.key == "extensions.skills.catalog"
                    for request in requests
                    for i in request.items
                )
                states = [
                    i
                    for i in stored
                    if isinstance(i, ContextItem) and i.key == "extensions.skills.catalog"
                ]
                assert len(states) == 2  # Initial baseline and post-compaction baseline.
                assert all(i.snapshot_state == "skills.hidden" and not i.content for i in states)
            events = [e async for e in runtime.stream("use $fixture again")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(selected(requests[-1].items)) == 1
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
