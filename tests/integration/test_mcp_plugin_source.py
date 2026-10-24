"""Native plugin source normalization reaches real process execution and tool recall."""

import asyncio
import json
import os
import shlex
import sys
from pathlib import Path

import pytest
from platform_commands import python_command
from test_mcp_bound_http import Carrier
from test_mcp_tool_approval import Model

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.mcp.http_redirect import MCPHttpSession
from corki.protocol.events import TurnCompleted
from corki.protocol.items import ToolResultItem
from corki.tools import ToolRegistry


def contains_cwd(value, expected):
    """Inspect nested tool JSON without depending on Python repr path escaping."""
    if isinstance(value, dict):
        return value.get("cwd") == expected or any(
            contains_cwd(item, expected) for item in value.values()
        )
    if isinstance(value, list):
        return any(contains_cwd(item, expected) for item in value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except ValueError:
            return False
        if decoded != value:
            return contains_cwd(decoded, expected)
    return False


@pytest.mark.parametrize("mode", ["direct", "native", "compatible", "code_mode"])
@pytest.mark.parametrize("placement", ["default_cwd", "relative_cwd", "bare_file", "bad_sibling"])
def test_plugin_source_search_call_and_cwd_are_preserved(tmp_path, mode, placement):
    async def scenario():
        project = tmp_path / "project"
        project.mkdir()
        root = project / ".corki" / "plugins" / "sample"
        manifest = root / ".codex-plugin" / "plugin.json"
        manifest.parent.mkdir(parents=True)
        script = Path(__file__).resolve().parents[1] / "fixtures" / "plugin_cwd_mcp_server.py"
        report = tmp_path / "process-report.json"
        server = {"command": sys.executable, "args": [str(script), str(report)]}
        expected = project
        if placement == "relative_cwd":
            expected = root / "work"
            expected.mkdir()
            server["cwd"] = "work"
        servers = {"docs": server}
        if placement == "bad_sibling":
            servers["bad"] = {"command": 42}
        value = {"name": "sample"}
        if placement == "bare_file":
            (root / ".mcp.json").write_text(json.dumps(servers))
        else:
            value["mcpServers"] = servers
        manifest.write_text(json.dumps(value))
        model = Model(mode)
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=project,
                plugin_dirs=(project / ".corki/plugins",),
                skills_enabled=False,
                api_mode="responses",
                tool_search_mode=mode if mode in ("native", "compatible") else "disabled",
                tool_mode="code_mode" if mode == "code_mode" else "direct",
            ),
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
            registry=ToolRegistry(),
            model=model,
            mcp_requirements={},
        )
        process = None
        try:
            await runtime._ensure_ready()
            await runtime._mcp_manager.refresh_if_dirty()
            assert runtime._mcp_manager.warnings == ()
            assert "docs" in runtime._mcp_manager._clients_by_name
            process = runtime._mcp_manager._clients_by_name["docs"].client._process
            events = [e async for e in runtime.stream("needle cwd")]
            assert isinstance(events[-1], TurnCompleted)
            results = [i for i in model.requests[-1].items if isinstance(i, ToolResultItem)]
            assert any(
                contains_cwd(getattr(part, "text", ""), str(expected.resolve()))
                for item in results
                for part in item.content_items
            )
            assert json.loads(report.read_text()) == {"cwd": str(expected.resolve()), "calls": 1}
            if mode in ("native", "compatible"):
                assert any(i.tool_name == "tool_search" for i in results)
            assert bool(runtime._plugin_manager.warnings) is (placement == "bad_sibling")
        finally:
            await runtime.aclose()
        assert process is not None and process.returncode is not None

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "native", "compatible", "code_mode"])
def test_plugin_http_helper_executes_in_runtime_directory(tmp_path, monkeypatch, mode):
    async def scenario():
        project = tmp_path / "project"
        project.mkdir()
        root = project / ".corki" / "plugins" / "sample"
        manifest = root / ".codex-plugin" / "plugin.json"
        manifest.parent.mkdir(parents=True)
        helper_source = "import json, os\nprint(json.dumps({'x-fixture-cwd': os.getcwd()}))\n"
        if os.name == "nt":
            helper_command = python_command(project, helper_source, name="auth.py")
        else:
            (project / "auth.py").write_text(helper_source, encoding="utf-8")
            helper_command = shlex.quote(sys.executable) + " auth.py"
        manifest.write_text(
            json.dumps(
                {
                    "name": "sample",
                    "mcpServers": {
                        "docs": {
                            "url": "https://fixture.invalid",
                            "http_headers_helper": helper_command,
                        }
                    },
                }
            )
        )
        carrier = Carrier()

        def session(**kwargs):
            kwargs["transport"] = carrier
            return MCPHttpSession(**kwargs)

        monkeypatch.setattr("corki.mcp.client.MCPHttpSession", session)
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=project,
                plugin_dirs=(project / ".corki/plugins",),
                skills_enabled=False,
                api_mode="responses",
                tool_search_mode=mode if mode in ("native", "compatible") else "disabled",
                tool_mode="code_mode" if mode == "code_mode" else "direct",
            ),
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
            registry=ToolRegistry(),
            model=Model(mode),
            mcp_requirements={},
        )
        try:
            await runtime._ensure_ready()
            await runtime._mcp_manager.refresh_if_dirty()
            assert runtime._mcp_manager.warnings == ()
            events = [e async for e in runtime.stream("needle")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(carrier.calls) == 1
            assert carrier.requests and all(
                r.headers.get("x-fixture-cwd") == str(project.resolve()) for r in carrier.requests
            )
        finally:
            await runtime.aclose()
        assert carrier.closed and all(b.closed for b in carrier.bodies)

    asyncio.run(scenario())
