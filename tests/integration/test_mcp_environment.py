import asyncio
import json
import sys
from pathlib import Path

import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import StdioMCPClient
from corki.models import ModelCompleted
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["native", "compatible"])
@pytest.mark.parametrize("plugin", [False, True])
def test_runtime_exports_selected_env_and_reconciles_only_referenced_values(
    tmp_path, monkeypatch, mode, plugin
):
    async def scenario():
        clients, processes, requests, seen = [], [], [], []
        script = Path(__file__).parents[1] / "fixtures" / "environment_mcp_server.py"
        monkeypatch.setenv("CORKI_MCP_ENV_EXPORT", "first")
        monkeypatch.setenv("CORKI_MCP_ENV_OVERRIDE", "ambient")
        monkeypatch.setenv("CORKI_MCP_UNREQUESTED", "fixture-private")

        class Client(StdioMCPClient):
            async def start(self):
                await super().start()
                processes.append(self._process)

        def factory(settings):
            client = Client(settings)
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        config = {
            "command": sys.executable,
            "args": [str(script)],
            "cwd": str(tmp_path),
            "env_vars": [
                "CORKI_MCP_ENV_EXPORT",
                {"name": "CORKI_MCP_ENV_OVERRIDE", "source": "local"},
            ],
            "env": {
                "CORKI_MCP_ENV_OVERRIDE": "literal",
                "node_repl_auth_token": "fixture-denied",
            },
        }
        settings = MCPServerSettings.from_mapping("docs", config)
        tool_name = "mcp__docs::lookup"
        if plugin:
            manifest = tmp_path / ".corki/plugins/fixture/.codex-plugin/plugin.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({"name": "fixture", "mcpServers": {"docs": config}}))
        runtime_settings = CorkiSettings(
            working_directory=tmp_path,
            plugin_dirs=(tmp_path / ".corki/plugins",),
            skills_enabled=False,
            mcp_servers=() if plugin else (settings,),
            api_mode="responses",
            tool_search_mode=mode,
        )

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                number = len(requests)
                if number == 1:
                    call = ToolCall(new_tool_call_id(), "tool_search", {"query": "needle"})
                else:
                    if number >= 3:
                        item = next(
                            i for i in reversed(request.items) if isinstance(i, ToolResultItem)
                        )
                        value = json.loads(item.content.split("\nOutput:\n", 1)[1])
                        expected = "first" if number == 3 else "second"
                        assert value == {
                            "exported": expected,
                            "overridden": "literal",
                            "unrequested_present": False,
                            "internal_present": False,
                            "pid": processes[-1].pid,
                        }
                        seen.append(value)
                    if number == 3:
                        monkeypatch.setenv("CORKI_MCP_ENV_EXPORT", "second")
                        runtime.request_mcp_reconcile()
                    elif number == 4:
                        assert len(clients) == 2 and seen[0]["pid"] != seen[1]["pid"]
                        monkeypatch.setenv("CORKI_MCP_UNREQUESTED", "unrelated-change")
                        runtime.request_mcp_reconcile()
                    elif number == 5:
                        assert len(clients) == 2 and seen[1]["pid"] == seen[2]["pid"]
                        yield ModelCompleted((AssistantMessageItem("done", turn, step),))
                        return
                    call = ToolCall(new_tool_call_id(), tool_name, {})
                yield ModelCompleted((ToolCallItem(call, turn, step),))

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=runtime_settings,
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
        )
        try:
            events = [e async for e in runtime.stream("environment needle")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            raw = await runtime._repository.load_items(runtime._thread_id)
            assert (
                len([i for i in raw if isinstance(i, ToolResultItem) and i.tool_name == tool_name])
                == 3
            )
        finally:
            await runtime.aclose()
        assert all(c.is_closed for c in clients) and all(
            p.returncode is not None for p in processes
        )

        class ColdModel:
            count = 0

            async def stream(self, request):
                self.count += 1
                turn, step = request.items[-1].turn_id, new_step_id()
                if self.count == 1:
                    old = [
                        i
                        for i in request.items
                        if isinstance(i, ToolResultItem) and i.tool_name == tool_name
                    ]
                    assert len(old) == 3
                    yield ModelCompleted(
                        (ToolCallItem(ToolCall(new_tool_call_id(), tool_name, {}), turn, step),)
                    )
                else:
                    assert self.count == 2
                    latest = next(
                        i for i in reversed(request.items) if isinstance(i, ToolResultItem)
                    )
                    value = json.loads(latest.content.split("\nOutput:\n", 1)[1])
                    assert value["exported"] == "second" and value["overridden"] == "literal"
                    assert not value["unrequested_present"] and not value["internal_present"]
                    assert value["pid"] == processes[-1].pid
                    assert value["pid"] != seen[-1]["pid"]
                    yield ModelCompleted((AssistantMessageItem("cold done", turn, step),))

            async def aclose(self):
                pass

        cold = await LangGraphRuntime.acreate(
            settings=runtime_settings,
            model=ColdModel(),
            registry=ToolRegistry(),
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
            thread_id=runtime._thread_id,
        )
        try:
            events = [e async for e in cold.stream("continue")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            after = await cold._repository.load_items(cold._thread_id)
            assert after[: len(raw)] == raw
            assert (
                len(
                    [i for i in after if isinstance(i, ToolResultItem) and i.tool_name == tool_name]
                )
                == 4
            )
        finally:
            await cold.aclose()
        assert len(clients) == len(processes) == 3
        assert all(c.is_closed for c in clients)
        assert all(p.returncode is not None for p in processes)

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["native", "compatible"])
def test_remote_env_reference_isolated_before_local_process_creation(tmp_path, monkeypatch, mode):
    async def scenario():
        from dataclasses import replace

        from corki.config import MCPEnvVar

        clients = []
        script = Path(__file__).parents[1] / "fixtures" / "environment_mcp_server.py"
        good = MCPServerSettings("good", "stdio", command=sys.executable, args=(str(script),))
        bad = replace(good, name="bad", env_vars=(MCPEnvVar("CORKI_MCP_REMOTE", "remote"),))
        monkeypatch.setenv("CORKI_MCP_REMOTE", "host-value-must-not-be-forwarded")

        def factory(settings):
            client = StdioMCPClient(settings)
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)

        class Model:
            count = 0

            async def stream(self, request):
                self.count += 1
                turn, step = request.items[-1].turn_id, new_step_id()
                assert len(clients) == 2 and clients[0]._process is None
                assert clients[0]._reader_task is None and clients[0]._stderr_task is None
                assert len(runtime._mcp_manager.warnings) == 1
                warning = runtime._mcp_manager.warnings[0]
                assert warning.startswith("bad:") and "requires remote MCP stdio" in warning
                assert "host-value-must-not-be-forwarded" not in warning
                if self.count == 1:
                    call = ToolCall(new_tool_call_id(), "tool_search", {"query": "needle"})
                elif self.count == 2:
                    found = next(
                        i for i in reversed(request.items) if isinstance(i, ToolResultItem)
                    )
                    assert [s.name for s in found.discovered_tools] == ["mcp__good::lookup"]
                    call = ToolCall(new_tool_call_id(), "mcp__good::lookup", {})
                else:
                    assert self.count == 3
                    result = next(
                        i for i in reversed(request.items) if isinstance(i, ToolResultItem)
                    )
                    assert result.tool_name == "mcp__good::lookup" and not result.is_error
                    yield ModelCompleted((AssistantMessageItem("isolated", turn, step),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, step),))

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                plugin_dirs=(tmp_path / ".corki/plugins",),
                skills_enabled=False,
                api_mode="responses",
                tool_search_mode=mode,
                mcp_servers=(bad, good),
                model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
        )
        try:
            events = [e async for e in runtime.stream("needle")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            process = clients[1]._process
            assert process is not None
        finally:
            await runtime.aclose()
        assert all(c.is_closed for c in clients)
        assert process.returncode is not None

    asyncio.run(scenario())
