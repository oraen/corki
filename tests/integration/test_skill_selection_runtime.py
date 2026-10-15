import asyncio

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
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("logical", [False, True], ids=["canonical-path", "discovery-symlink"])
def test_repaired_skill_path_selection_is_injected_once_and_survives_cold_reopen(tmp_path, logical):
    async def scenario():
        project = tmp_path / "project"
        root = project / ".corki/skills"
        for name in ("alpha", "PATH"):
            path = root / name / "SKILL.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                f"---\nname: {name}\ndescription: fixture\n---\nBODY {name}", encoding="utf-8"
            )
        beta = tmp_path / "external/beta/SKILL.md"
        beta.parent.mkdir(parents=True)
        beta.write_text("---\ndescription: Build for AWS: ECS\n---\nBODY beta", encoding="utf-8")
        link = root / "linked"
        link.symlink_to(beta.parent, target_is_directory=True)
        locator = link / "SKILL.md" if logical else beta
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    items = (
                        ToolCallItem(
                            ToolCall(new_tool_call_id(), "skill_read", {"name": "beta"}), turn, step
                        ),
                    )
                else:
                    items = (AssistantMessageItem("done", turn, step),)
                yield ModelCompleted(items)

            async def aclose(self):
                pass

        async def create(thread_id=None):
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(working_directory=project),
                database_path=tmp_path / "sessions.db",
                home_path=tmp_path / "home",
                model=Model(),
                registry=ToolRegistry(),
                thread_id=thread_id,
            )

        runtime = await create()
        try:
            events = [
                e
                async for e in runtime.stream(
                    f"Use [$alpha](skill://{locator}) and leave $PATH alone"
                )
            ]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            first = requests[0].items
            bodies = [
                item for item in first if isinstance(item, ContextItem) and item.source_input_id
            ]
            assert len(bodies) == 1 and "<name>beta</name>" in bodies[0].content
            user = next(item for item in first if isinstance(item, UserMessageItem))
            assert bodies[0].source_input_id == user.id and first.index(bodies[0]) > first.index(
                user
            )
            assert any(
                isinstance(item, ToolResultItem) and "BODY beta" in item.content
                for item in requests[1].items
            )
            thread = runtime.thread_id
            await runtime.aclose()
            runtime = await create(thread)
            events = [e async for e in runtime.stream("Use $alpha now")]
            assert isinstance(events[-1], TurnCompleted)
            following = [
                item
                for item in requests[-1].items
                if isinstance(item, ContextItem) and item.source_input_id
            ]
            assert following[0] == bodies[0] and len(following) == 2
            assert "<name>alpha</name>" in following[1].content
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
