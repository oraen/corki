"""Real Runtime teardown/recreation, deterministic models and isolated user data."""

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
    ToolCallItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall


@pytest.mark.parametrize("followup", [False, True])
def test_recreated_parent_closes_or_explicitly_resumes_known_child(tmp_path, monkeypatch, followup):
    requests, child_ids = [], []

    class ChildModel:
        async def stream(self, request):
            requests.append(request)
            yield ModelCompleted((AssistantMessageItem(
                "verified child result", request.items[-1].turn_id, new_step_id()
            ),))

        async def aclose(self):
            pass

    class ParentModel(ChildModel):
        def __init__(self, actions):
            self.actions = list(actions)
            self.expected = None

        async def stream(self, request):
            if self.expected:
                results = [i for i in request.items
                           if getattr(i, "tool_name", "") == self.expected]
                assert results and not results[-1].is_error, results
                result = json.loads(results[-1].content)
                if self.expected == "spawn_agent":
                    child_ids.append(result["id"])
                if self.expected == "wait_agents":
                    assert not result["timed_out"], result
                    assert result["agents"][0]["result"] == "verified child result"
            if not self.actions:
                yield ModelCompleted((AssistantMessageItem(
                    "parent done", request.items[-1].turn_id, new_step_id()
                ),))
                return
            name = self.actions.pop(0)
            args = {
                "spawn_agent": lambda: {"message": "initial child instruction"},
                "send_agent_message": lambda: {"id": child_ids[0], "message": "new followup"},
                "wait_agents": lambda: {"ids": [child_ids[0]], "timeout_ms": 15000},
                "close_agent": lambda: {"id": child_ids[0]},
            }[name]()
            self.expected = name
            yield ModelCompleted((ToolCallItem(
                ToolCall(new_tool_call_id(), name, args),
                request.items[-1].turn_id, new_step_id(),
            ),))

    monkeypatch.setattr("corki.core.runtime._create_model", lambda *a: ChildModel())

    async def run():
        settings = CorkiSettings(
            working_directory=tmp_path, skills_enabled=False, plugins_enabled=False,
            memories_enabled=False, hooks_enabled=False, goals_enabled=False,
        )
        kwargs = dict(settings=settings, database_path=tmp_path / "sessions/core.db",
                      home_path=tmp_path)
        first = await LangGraphRuntime.acreate(
            **kwargs, model=ParentModel(["spawn_agent", "wait_agents"]),
        )
        parent_id = first.thread_id
        try:
            events = [e async for e in first.stream("delegate")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 1
        finally:
            await first.aclose()
        # Fresh root/controller, same durable session. No child models start at creation.
        second = await LangGraphRuntime.acreate(
            **kwargs, thread_id=parent_id,
            model=ParentModel(["send_agent_message", "wait_agents", "close_agent"]
                              if followup else ["close_agent", "close_agent"]),
        )
        try:
            assert len(requests) == 1
            events = [e async for e in second.stream("continue with saved child")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == (2 if followup else 1)
            if followup:
                users = [i.content for i in requests[-1].items if isinstance(i, UserMessageItem)]
                assert users == ["initial child instruction", "new followup"]
                assert any(i.key == "corki.subagent" for i in requests[-1].context_items)
            assert not second._agents.children
        finally:
            await second.aclose()

    asyncio.run(run())
