"""Pending discovered calls survive checkpoints, but not obsolete execution authority."""

import asyncio

import pytest
from test_mcp_environment_binding import server
from test_mcp_server_requirements import FinalModel, make_runtime
from test_mcp_tool_approval import Model

from corki.config import CorkiSettings, managed_mcp
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_turn_id
from corki.protocol.items import ToolCallItem, ToolResultItem, UserMessageItem
from corki.sessions import TurnRecord, TurnStatus
from corki.tools import ToolRegistry
from corki.tools.search import ToolSearchTool


@pytest.mark.parametrize("mode", ["native", "compatible"])
@pytest.mark.parametrize("restriction", ["environment", "managed_file"])
def test_pending_call_rechecks_current_authority_without_resampling_or_replaying_search(
    tmp_path, monkeypatch, mode, restriction
):
    path = tmp_path / "requirements.toml"
    path.write_text('[mcp_servers.docs.identity]\nurl="https://fixture.invalid"', encoding="utf-8")
    monkeypatch.setattr(managed_mcp, "system_requirements_path", lambda: path)

    class Sink:
        async def emit(self, event):
            pass

    async def scenario():
        first, clients, first_model = await make_runtime(
            tmp_path,
            monkeypatch,
            requirements=None,
            servers=(server(),),
            model=Model(mode),
            mode=mode,
        )
        try:
            await first._ensure_ready()
            thread, turn = first.thread_id, new_turn_id()
            user = UserMessageItem("needle", turn)
            await first._repository.save_turn(
                TurnRecord(turn, thread, TurnStatus.RUNNING, user.content)
            )
            config = first._graph_config(turn)
            context = GraphRunContext(events=Sink())
            # Complete discovery, then stop after the next model commit, before
            # the actual MCP call claim. This is a real persisted graph checkpoint.
            await first._compiled.ainvoke(
                _initial_state(thread, turn, first._settings, user),
                config=config,
                context=context,
                interrupt_after=["execute_tools"],
            )
            await first._compiled.ainvoke(
                None,
                config=config,
                context=context,
                interrupt_before=["execute_tools"],
            )
            checkpoint = await first._compiled.aget_state(config)
            assert checkpoint.next == ("execute_tools",)
            assert len(first_model.requests) == 2
            before = await first._repository.load_items(thread)
            pending = next(
                item
                for item in before
                if isinstance(item, ToolCallItem) and item.call.name != "tool_search"
            )
            assert any(
                isinstance(item, ToolResultItem) and item.discovered_tools for item in before
            )
            assert len(clients) == 1 and clients[0].calls == []
        finally:
            await first.aclose()
        if restriction == "managed_file":
            path.write_text("[mcp_servers]", encoding="utf-8")

        searches = []

        async def forbidden_search(*args, **kwargs):
            searches.append(True)
            raise AssertionError("persisted discovery must not execute again")

        monkeypatch.setattr(ToolSearchTool, "execute", forbidden_search)
        model = FinalModel()
        resumed = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                api_mode="responses",
                tool_search_mode=mode,
                model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
                mcp_servers=(
                    server("missing-remote" if restriction == "environment" else "local"),
                ),
            ),
            model=model,
            registry=ToolRegistry(),
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
            thread_id=thread,
        )
        try:
            events = [event async for event in resumed.resume_pending()]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(clients) == 1 and clients[0].calls == []
            assert searches == [] and len(model.requests) == 1
            after = await resumed._repository.load_items(thread)
            assert after[: len(before)] == before
            results = [
                item
                for item in after
                if isinstance(item, ToolResultItem) and item.call_id == pending.call.id
            ]
            assert len(results) == 1 and results[0].is_error
            assert sum(isinstance(item, UserMessageItem) for item in after) == 1
            assert sum(isinstance(item, ToolCallItem) for item in after) == 2
            assert [event async for event in resumed.resume_pending()] == []
            assert searches == [] and len(model.requests) == 1
        finally:
            await resumed.aclose()
        assert clients[0].closed

    asyncio.run(scenario())
