import asyncio
import json

import pytest

from corki.config import CorkiSettings
from corki.config.skills import SkillRule
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
from corki.tools import ToolRegistry


@pytest.mark.parametrize("disabled", [False, True], ids=["hidden-enabled", "hidden-disabled"])
def test_runtime_distinguishes_hidden_explicit_selection_from_disabled(tmp_path, disabled):
    async def scenario():
        path = tmp_path / ".corki/skills/fixture/SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(
            "---\nname: fixture\ndescription: HIDDEN DESCRIPTION\n---\nPRIVATE BODY",
            encoding="utf-8",
        )
        policy = path.parent / "agents/openai.yaml"
        policy.parent.mkdir()
        policy.write_text("policy:\n  allow_implicit_invocation: false\n", encoding="utf-8")
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) < 3:
                    name = "skill_list" if len(requests) == 1 else "skill_read"
                    args = {} if name == "skill_list" else {"name": "fixture"}
                    items = (ToolCallItem(ToolCall(new_tool_call_id(), name, args), turn, step),)
                else:
                    items = (AssistantMessageItem("done", turn, step),)
                yield ModelCompleted(items)

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_config=(SkillRule(False, name="fixture"),) if disabled else (),
            ),
            database_path=tmp_path / "sessions.db",
            home_path=tmp_path / ".corki",
            model=Model(),
            registry=ToolRegistry(),
        )
        try:
            events = [e async for e in runtime.stream("Use $fixture")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            for request in requests:
                catalogs = [
                    item.content
                    for item in request.items
                    if isinstance(item, ContextItem) and item.key == "extensions.skills.catalog"
                ]
                assert all("HIDDEN DESCRIPTION" not in item for item in catalogs)
            bodies = [
                item
                for item in requests[0].items
                if isinstance(item, ContextItem) and item.source_input_id is not None
            ]
            assert len(bodies) == (0 if disabled else 1)
            results = [item for item in requests[-1].items if isinstance(item, ToolResultItem)]
            listed = next(item for item in results if item.tool_name == "skill_list")
            assert "fixture" not in {
                skill["name"] for skill in json.loads(listed.content)["skills"]
            }
            read = next(item for item in results if item.tool_name == "skill_read")
            assert read.is_error is disabled
            assert ("PRIVATE BODY" in read.content) is not disabled
            if disabled:
                assert "PRIVATE BODY" not in repr(
                    await runtime._repository.load_items(runtime.thread_id)
                )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
