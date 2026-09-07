import asyncio

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted, WarningEvent
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    ToolCallItem,
    ToolResultItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


def test_hidden_automatic_catalog_preserves_explicit_input_and_requested_tools(tmp_path):
    async def scenario():
        skill = tmp_path / ".corki/skills/fixture/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(
            "---\ndescription: Fixture description\n---\nBODY fixture", encoding="utf-8"
        )
        config = tmp_path / "config.toml"
        config.write_text("[skills]\ninclude_instructions = false\nmax_context_tokens = 1\n")
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                index = len(requests)
                if index <= 2:
                    name = "skill_list" if index == 1 else "skill_read"
                    args = {} if index == 1 else {"name": "fixture"}
                    items = (ToolCallItem(ToolCall(new_tool_call_id(), name, args), turn, step),)
                else:
                    items = (AssistantMessageItem("done", turn, step),)
                yield ModelCompleted(items)

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings.for_directory(tmp_path, config_file=config),
            database_path=tmp_path / "sessions.db",
            home_path=tmp_path / "home",
            model=Model(),
            registry=ToolRegistry(),
        )
        try:
            events = [e async for e in runtime.stream("Use $fixture")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert not any(
                isinstance(item, ContextItem) and item.key == "extensions.skills.catalog"
                for request in requests
                for item in request.items
            )
            assert not any(isinstance(e, WarningEvent) for e in events)
            bodies = [
                i for i in requests[0].items if isinstance(i, ContextItem) and i.source_input_id
            ]
            assert len(bodies) == 1 and "BODY fixture" in bodies[0].content
            results = [i for i in requests[-1].items if isinstance(i, ToolResultItem)]
            assert len(results) == 2 and all(not i.is_error for i in results)
            assert "Fixture description" in results[0].content
            assert "BODY fixture" in results[1].content
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("initially_empty", [False, True])
def test_catalog_state_transitions_survive_cold_reopen(tmp_path, monkeypatch, initially_empty):
    # Isolate project skill discovery from installation of bundled fixtures;
    # Runtime still constructs the real service, contributor and window manager.
    monkeypatch.setattr("corki.skills.service.install_bundled_skills", lambda home: None)

    async def scenario():
        skill = tmp_path / ".corki/skills/fixture/SKILL.md"
        skill.parent.mkdir(parents=True)
        document = "---\ndescription: Fixture description\n---\nBODY fixture"
        if not initially_empty:
            skill.write_text(document)
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        thread = None
        previous_request = ()
        expected_catalogs = []
        for stage, include in enumerate((True, False, False, True, True)):
            if stage == 3 and skill.exists():
                skill.unlink()
            if stage == 4:
                skill.write_text(document)
            runtime = LangGraphRuntime.create(
                settings=CorkiSettings(
                    working_directory=tmp_path, skills_include_instructions=include
                ),
                database_path=tmp_path / "sessions.db",
                home_path=tmp_path / "home",
                model=Model(),
                registry=ToolRegistry(),
                thread_id=thread,
            )
            try:
                events = [e async for e in runtime.stream("continue")]
                assert isinstance(events[-1], TurnCompleted), events[-1]
                assert not any(isinstance(e, WarningEvent) for e in events)
                current = requests[-1].items
                assert current[: len(previous_request)] == previous_request
                catalogs = [
                    i
                    for i in current
                    if isinstance(i, ContextItem) and i.key == "extensions.skills.catalog"
                ]
                if stage == 0 and not initially_empty or stage in (1, 3, 4):
                    expected_catalogs.append(catalogs[-1])
                assert catalogs == expected_catalogs
                if stage == 1:
                    assert "not listed automatically" in catalogs[-1].content
                    assert catalogs[-1].snapshot_content == ""
                elif stage == 3:
                    assert "No host skills are currently available" in catalogs[-1].content
                elif stage == 4:
                    assert "Fixture description" in catalogs[-1].content
                stored = await runtime._repository.load_items(runtime.thread_id)
                states = [
                    i
                    for i in stored
                    if isinstance(i, ContextItem) and i.key == "extensions.skills.catalog"
                ]
                assert len(states) == (1, 2, 2, 3, 4)[stage]
                assert states[-1].snapshot_state == (
                    "skills.listed" if include else "skills.hidden"
                )
                if stage == 0 and initially_empty:
                    assert states[0].content == "" and states[0] not in current
                thread, previous_request = runtime.thread_id, current
            finally:
                await runtime.aclose()

    asyncio.run(scenario())
