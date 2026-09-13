"""Old flat identities stay durable facts; migration must not replay their effects."""

import asyncio

import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import MCPClient
from corki.models import ModelCompleted
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall, ToolExposure, ToolResult, ToolSpec
from corki.sessions import TurnRecord, TurnStatus
from corki.tools import ToolRegistry


@pytest.mark.parametrize("outcome", ["completed", "unknown", "unclaimed"])
@pytest.mark.parametrize("mode", ["compatible", "native"])
def test_cold_resume_old_call_retains_ledger_outcome_then_searches_new_identity(
    tmp_path, monkeypatch, outcome, mode
):
    async def scenario():
        executed, requests = [], []

        class Client(MCPClient):
            async def start(self):
                pass

            async def list_tools(self):
                return ({"name": "lookup", "description": "needle", "inputSchema": {}},)

            async def call_tool(self, name, arguments):
                executed.append(name)
                return {"content": [{"type": "text", "text": "NEW RESULT"}]}

            async def _exchange(self, message):
                raise AssertionError(message)

            async def _send_notification(self, message):
                raise AssertionError(message)

            async def aclose(self):
                pass

        monkeypatch.setattr("corki.mcp.manager.create_client", Client)

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    previous_search = next(
                        i
                        for i in request.items
                        if isinstance(i, ToolResultItem) and i.tool_name == "tool_search"
                    )
                    assert previous_search.discovered_tools == ()
                    assert legacy_spec.name not in {tool.name for tool in request.tools}
                    old = next(
                        i
                        for i in request.items
                        if isinstance(i, ToolResultItem) and i.tool_name == "mcp__docs__lookup"
                    )
                    assert not executed
                    if outcome == "completed":
                        assert old.content == "OLD DURABLE RESULT" and not old.is_error
                    elif outcome == "unknown":
                        assert old.is_error and "outcome is unknown" in old.content
                    else:
                        assert old.is_error and "not advertised" in old.content
                    item = ToolCallItem(
                        ToolCall(new_tool_call_id(), "tool_search", {"query": "needle"}), turn, step
                    )
                elif len(requests) == 2:
                    search = next(
                        i
                        for i in reversed(request.items)
                        if isinstance(i, ToolResultItem) and i.tool_name == "tool_search"
                    )
                    assert [s.name for s in search.discovered_tools] == ["mcp__docs::lookup"]
                    assert "mcp__docs::lookup" in {tool.name for tool in request.tools}
                    item = ToolCallItem(
                        ToolCall(new_tool_call_id(), "mcp__docs::lookup", {}), turn, step
                    )
                else:
                    assert executed == ["lookup"]
                    item = AssistantMessageItem("done", turn, step)
                yield ModelCompleted((item,))

            async def aclose(self):
                pass

        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            api_mode="responses",
            tool_search_mode=mode,
            model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
        )
        legacy_spec = ToolSpec(
            "mcp__docs__lookup", "needle", {}, exposure=ToolExposure.DEFERRED, source="docs"
        )
        first = LangGraphRuntime.create(
            settings=settings,
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
            registry=ToolRegistry(),
            model=Model(),
        )
        try:
            await first._ensure_ready()
            turn = new_turn_id()
            user = UserMessageItem("recover then search again", turn)
            repo = first._repository
            await repo.save_turn(
                TurnRecord(turn, first.thread_id, TurnStatus.RUNNING, user.content)
            )
            await repo.append_items(first.thread_id, (user,))
            search_call = ToolCall(new_tool_call_id(), "tool_search", {"query": "needle"})
            await repo.append_items(
                first.thread_id,
                (
                    ToolCallItem(search_call, turn, new_step_id()),
                    ToolResultItem(
                        search_call.id,
                        "tool_search",
                        "legacy discovery",
                        turn,
                        discovered_tools=(legacy_spec,),
                    ),
                ),
            )
            old_call = ToolCall(new_tool_call_id(), "mcp__docs__lookup", {})
            await repo.commit_model_step(
                first.thread_id,
                turn,
                0,
                ModelCompleted((ToolCallItem(old_call, turn, new_step_id()),)),
            )
            if outcome != "unclaimed":
                assert await repo.claim_tool_call(first.thread_id, turn, old_call) is None
            if outcome == "completed":
                await repo.complete_tool_call(
                    first.thread_id,
                    turn,
                    ToolResult(old_call.id, old_call.name, "OLD DURABLE RESULT"),
                )
            before = await repo.load_items(first.thread_id)
        finally:
            await first.aclose()
        second = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                api_mode="responses",
                tool_search_mode=mode,
                model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
                mcp_servers=(MCPServerSettings("docs", "http", url="https://fixture.invalid"),),
            ),
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
            thread_id=first.thread_id,
            registry=ToolRegistry(),
            model=Model(),
        )
        try:
            events = [e async for e in second.resume_pending()]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 3 and executed == ["lookup"]
            after = await second._repository.load_items(second.thread_id)
            assert after[: len(before)] == before
        finally:
            await second.aclose()

    asyncio.run(scenario())
