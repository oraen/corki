"""Dependency installation refreshes the saved global snapshot, not just new names."""

import asyncio
import tomllib

import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.config.mcp_requirements import MCPRequirements, MCPServerIdentity
from corki.core import LangGraphRuntime
from corki.mcp.client import MCPClient
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import ToolCallId
from corki.protocol.items import ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("case", ["merge", "blocked", "disabled", "current_disabled", "no_added"])
@pytest.mark.parametrize("search_mode", ["disabled", "compatible"])
def test_install_merges_global_servers_without_replacing_current_authority(
    tmp_path, monkeypatch, case, search_mode
):
    async def scenario():
        skill = tmp_path / ".corki/skills/install-guide/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: install-guide\ndescription: fixture\n---\nBODY")
        metadata = skill.parent / "agents/openai.yaml"
        metadata.parent.mkdir()
        metadata.write_text(
            "dependencies:\n  tools:\n    - type: mcp\n      value: added\n"
            "      transport: stdio\n      command: fixture-added\n"
        )
        home = tmp_path / "home"
        home.mkdir()
        clients, requests, calls = [], [], []
        phase = 0

        class Client(MCPClient):
            def __init__(self, settings):
                super().__init__(settings)
                self.closed = False
                clients.append(self)

            async def start(self):
                pass

            async def list_tools(self):
                return [{"name": "lookup", "inputSchema": {"type": "object"}}]

            async def call_tool(self, name, arguments):
                calls.append(self.settings.name)
                return {"content": [{"type": "text", "text": "GLOBAL_MERGE_OBSERVATION"}]}

            async def aclose(self):
                self.closed = True

            async def _exchange(self, message):
                raise AssertionError(message)

            async def _send_notification(self, message):
                raise AssertionError(message)

        monkeypatch.setattr("corki.mcp.manager.create_client", Client)

        class Model:
            searched = False

            async def stream(self, request):
                requests.append((phase, request))
                name = "mcp__global::lookup"
                if phase == 1 and search_mode == "compatible" and not self.searched:
                    assert name not in {t.name for t in request.tools}
                    assert "tool_search" in {t.name for t in request.tools}
                    self.searched = True
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(
                                    ToolCallId("global-search"), "tool_search", {"query": "lookup"}
                                ),
                                request.items[-1].turn_id,
                                new_step_id(),
                            ),
                        )
                    )
                elif phase == 1 and not calls and any(t.name == name for t in request.tools):
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(ToolCallId("global-merge"), name, {}),
                                request.items[-1].turn_id,
                                new_step_id(),
                            ),
                        )
                    )
                else:
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                execution_permissions=None,
                tool_search_mode=search_mode,
                mcp_servers=(
                    MCPServerSettings(
                        "current",
                        "stdio",
                        command="fixture-current",
                        enabled=case != "current_disabled",
                    ),
                ),
            ),
            model=Model(),
            registry=ToolRegistry(),
            home_path=home,
            database_path=tmp_path / "session.db",
        )
        # A valid global edit after Runtime construction is not in its current snapshot.
        path = home / "config.toml"
        path.write_text(
            '# keep\n[mcp.servers.current]\ncommand="must-not-replace-current"\n'
            '[mcp.servers.global]\ncommand="fixture-global"\n'
            + ("enabled=false\n" if case == "disabled" else "")
            + (
                '[mcp.servers.added]\ncommand="fixture-already-present"\n'
                if case == "no_added"
                else ""
            )
        )
        before = path.read_text()
        if case == "blocked":
            runtime._mcp_manager._requirements = MCPRequirements(
                servers=(
                    ("current", MCPServerIdentity("stdio", "fixture-current")),
                    ("added", MCPServerIdentity("stdio", "fixture-added")),
                )
            )
        try:
            first = [e async for e in runtime.stream("$install-guide")]
            assert isinstance(first[-1], TurnCompleted)
            phase = 1
            second = [e async for e in runtime.stream("Use available tools")]
            assert isinstance(second[-1], TurnCompleted)
            expected_global = case in {"merge", "current_disabled"}
            assert ("global" in {c.settings.name for c in clients}) == expected_global
            assert calls == (["global"] if expected_global else [])
            assert (
                any(
                    isinstance(item, ToolResultItem) and "GLOBAL_MERGE_OBSERVATION" in item.content
                    for turn, request in requests
                    if turn == 1
                    for item in request.items
                )
                == expected_global
            )
            assert all(c.settings.command != "must-not-replace-current" for c in clients)
            assert sum(c.settings.name == "current" for c in clients) == (
                0 if case == "current_disabled" else 1
            ), "merging global declarations must not restart an unchanged client"
            assert ("current" in {c.settings.name for c in clients}) == (case != "current_disabled")
            assert ("added" in {c.settings.name for c in clients}) == (case != "no_added")
            if case == "no_added":
                assert path.read_text() == before
            saved = tomllib.loads(path.read_text())["mcp"]["servers"]
            assert saved["current"]["command"] == "must-not-replace-current"
            assert "# keep" in path.read_text()
        finally:
            await runtime.aclose()
        assert all(c.closed for c in clients)

    asyncio.run(asyncio.wait_for(scenario(), 10))
