"""Memory procedures keep path identity, separate from ordinary skill names."""

import asyncio
import json

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    ToolCallItem,
    ToolResultItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall


@pytest.mark.parametrize("ordinary_exists", [False, True])
def test_memory_skill_name_failure_recovers_by_exact_path(tmp_path, ordinary_exists):
    async def scenario():
        home = tmp_path / "home"
        # The active memory root may be overridden: never assume home/memories.
        root = tmp_path / "custom memory 中文"
        relative = "skills/weather-lookup-wttr-jq/SKILL.md"
        target = root / relative
        target.parent.mkdir(parents=True)
        target.write_text(
            "---\nname: weather-lookup-wttr-jq\ndescription: Weather\n---\nMEMORY_BODY"
        )
        (root / "memory_summary.md").write_text("Weather procedure is indexed in MEMORY.md.")
        (root / "MEMORY.md").write_text(f"Related skill: {relative}")
        if ordinary_exists:
            ordinary = home / relative
            ordinary.parent.mkdir(parents=True)
            ordinary.write_text(
                "---\nname: weather-lookup-wttr-jq\ndescription: Other weather\n---\nORDINARY_BODY"
            )

        class Model:
            calls = 0

            async def stream(self, request):
                self.calls += 1
                turn, step = request.items[-1].turn_id, new_step_id()
                results = [i for i in request.items if isinstance(i, ToolResultItem)]
                if self.calls == 1:
                    skill_read = next(t for t in request.tools if t.name == "skill_read")
                    assert "no pagination" in skill_read.description
                    assert "bounded parts until EOF before acting" in skill_read.description
                    context = next(
                        i.content
                        for i in request.items
                        if isinstance(i, ContextItem) and i.key == "memory.instructions"
                    )
                    assert "Memory skills are memory files" in context
                    assert str(root) in context
                    assert "even if an ordinary skill has the same name" in context
                    name, args = "skill_read", {"name": "weather-lookup-wttr-jq"}
                elif self.calls == 2:
                    result = results[-1]
                    if ordinary_exists:
                        assert not result.is_error and "ORDINARY_BODY" in result.content
                    else:
                        assert result.is_error and "memories::read" in result.content
                        assert "Do not guess a memory root" in result.content
                    assert "MEMORY_BODY" not in result.content
                    name, args = "memories::read", {"path": "MEMORY.md"}
                elif self.calls == 3:
                    assert not results[-1].is_error
                    assert relative in json.loads(results[-1].content)["content"]
                    name, args = "memories::read", {"path": relative}
                else:
                    assert self.calls == 4 and not results[-1].is_error
                    assert "MEMORY_BODY" in json.loads(results[-1].content)["content"]
                    yield ModelCompleted(
                        (AssistantMessageItem("Read the memory procedure", turn, step),)
                    )
                    return
                yield ModelCompleted(
                    (ToolCallItem(ToolCall(new_tool_call_id(), name, args), turn, step),)
                )

            async def aclose(self):
                pass

        model = Model()
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                plugins_enabled=False,
                memories_enabled=True,
                memories_generate=False,
                memories_background_enabled=False,
                memories_dedicated_tools=True,
            ),
            database_path=tmp_path / "sessions.db",
            home_path=home,
            memory_root=root,
            model=model,
        )
        try:
            events = [event async for event in runtime.stream("Find the prior weather procedure")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert model.calls == 4
            assert target.read_text().endswith("MEMORY_BODY")
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
