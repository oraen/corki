"""Real parent/child runtimes with isolated storage and deterministic models."""

import asyncio
import json

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, new_step_id
from corki.protocol.tools import ToolCall


@pytest.mark.parametrize("execute", [False, True])
def test_parent_delegates_and_collects_real_child(tmp_path, monkeypatch, execute):
    child_requests = []

    class ChildModel:
        async def stream(self, request):
            child_requests.append(request)
            if execute:
                outputs = [
                    i for i in request.items if getattr(i, "tool_name", "") == "exec_command"
                ]
                if not outputs:
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(
                                    new_tool_call_id(),
                                    "exec_command",
                                    {
                                        "cmd": "printf child-command-result",
                                        "login": False,
                                    },
                                ),
                                request.items[-1].turn_id,
                                new_step_id(),
                            ),
                        )
                    )
                    return
                assert not outputs[-1].is_error, outputs[-1].content
                assert "child-command-result" in outputs[-1].content
            yield ModelCompleted(
                (
                    AssistantMessageItem(
                        "child verified result", request.items[-1].turn_id, new_step_id()
                    ),
                )
            )

        async def aclose(self):
            pass

    class ParentModel(ChildModel):
        async def stream(self, request):
            results = [i for i in request.items if getattr(i, "tool_name", "") == "spawn_agent"]
            waits = [i for i in request.items if getattr(i, "tool_name", "") == "wait_agents"]
            if not results:
                name, args = "spawn_agent", {"message": "Return a verified result"}
            elif not waits or json.loads(waits[-1].content)["timed_out"]:
                identity = json.loads(results[-1].content)["id"]
                name, args = "wait_agents", {"ids": [identity], "timeout_ms": 1000}
            else:
                assert "child verified result" in waits[-1].content
                yield ModelCompleted(
                    (
                        AssistantMessageItem(
                            "parent collected result", request.items[-1].turn_id, new_step_id()
                        ),
                    )
                )
                return
            yield ModelCompleted(
                (
                    ToolCallItem(
                        ToolCall(new_tool_call_id(), name, args),
                        request.items[-1].turn_id,
                        new_step_id(),
                    ),
                )
            )

    monkeypatch.setattr("corki.core.runtime._create_model", lambda *a: ChildModel())

    async def run():
        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            memories_enabled=False,
            hooks_enabled=False,
        )
        parent = await LangGraphRuntime.acreate(
            settings=settings,
            model=ParentModel(),
            database_path=tmp_path / "sessions/corki.db",
            home_path=tmp_path,
        )
        try:
            events = [e async for e in parent.stream("parent private context")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert events[-1].final_answer == "parent collected result"
            assert len(child_requests) == (2 if execute else 1)
            assert any(i.key == "corki.subagent" for i in child_requests[0].context_items)
            assert not any(t.name == "spawn_agent" for t in child_requests[0].tools)
            assert not any(
                getattr(i, "content", "") == "parent private context"
                for i in child_requests[0].items
            )
            child = next(iter(parent._agents.children.values()))
            assert child.runtime.thread_id != parent.thread_id
            assert child.runtime._session_source.is_non_root_agent
        finally:
            await parent.aclose()
        assert not parent._agents.children

    asyncio.run(run())
