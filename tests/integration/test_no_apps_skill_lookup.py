import asyncio

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.mcp.manager import MCPManager
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.items import AssistantMessageItem, new_step_id
from corki.tools import ToolRegistry


def test_normal_input_does_not_read_official_connector_directory(tmp_path, monkeypatch):
    async def scenario():
        async def forbidden(*args, **kwargs):
            raise AssertionError("Apps directory entered ordinary input preparation")

        monkeypatch.setattr(MCPManager, "capture_skill_connector_names", forbidden, raising=False)
        monkeypatch.setattr(MCPManager, "skill_connector_names", forbidden, raising=False)

        class Model:
            async def stream(self, request):
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(tmp_path, skills_enabled=False),
            model=Model(),
            database_path=tmp_path / "session.db",
            registry=ToolRegistry(),
        )
        try:
            events = [event async for event in runtime.stream("hello")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
