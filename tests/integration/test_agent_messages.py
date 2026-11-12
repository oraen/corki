"""Actual child steering, isolated state and a deterministic in-process model."""

import asyncio

from corki.agents.controller import AgentController
from corki.config import CorkiSettings
from corki.core.runtime import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.ids import ThreadId
from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id
from corki.tools.registry import ToolRegistry


def test_running_child_accepts_input_once_and_reuses_history_for_followup(tmp_path):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 1:
                    entered.set()
                    await release.wait()
                turn = next(
                    i.turn_id for i in reversed(request.items) if isinstance(i, UserMessageItem)
                )
                yield ModelCompleted((AssistantMessageItem("fixture reply", turn, new_step_id()),))

            async def aclose(self):
                pass

        async def factory(identity, context):
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    tmp_path,
                    skills_enabled=False,
                    plugins_enabled=False,
                    goals_enabled=False,
                    hooks_enabled=False,
                    memories_enabled=False,
                ),
                model=Model(),
                registry=ToolRegistry(),
                database_path=tmp_path / "core.db",
                home_path=tmp_path,
                thread_id=ThreadId(identity),
            )

        owner = AgentController(factory)
        try:
            child = await owner.spawn("first fixture input", None)
            await asyncio.wait_for(entered.wait(), 15)
            runtime = owner.get(child["id"]).runtime
            assert runtime._realtime.active
            await owner.send(child["id"], "supplement fixture input")
            release.set()
            result = await owner.wait([child["id"]], 15000)
            assert result["agents"][0]["status"] == "completed"
            assert len(requests) == 2
            history = await runtime._repository.load_items(runtime.thread_id)
            users = [i.content for i in history if isinstance(i, UserMessageItem)]
            assert users == ["first fixture input", "supplement fixture input"]
            await owner.send(child["id"], "idle followup fixture")
            result = await owner.wait([child["id"]], 15000)
            assert result["agents"][0]["status"] == "completed"
            history = await runtime._repository.load_items(runtime.thread_id)
            assert [i.content for i in history if isinstance(i, UserMessageItem)] == [
                *users,
                "idle followup fixture",
            ]
            assert owner.get(child["id"]).runtime is runtime
            assert not owner.get(child["id"]).pending
        finally:
            release.set()
            await owner.aclose()

    asyncio.run(run())
