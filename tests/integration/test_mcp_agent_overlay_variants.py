"""Raw JSON untagged variant selection controls Agent MCP overlay authority."""

import asyncio
import json
import sys
from pathlib import Path

import pytest
from test_mcp_agent_plugin import MCP_SCHEMA, SCHEMA, package, runtime_for
from test_mcp_bound_http import Carrier

from corki.plugins import PluginManager
from corki.protocol.events import TurnCompleted
from corki.protocol.items import ToolResultItem
from corki.tools import ToolRegistry


@pytest.mark.parametrize(
    ("number", "fallback"),
    [
        ("0", True),
        ("-0", True),
        ("18446744073709551615", True),
        ("-9223372036854775808", True),
        ("18446744073709551616", False),
        ("-9223372036854775809", False),
        ("1.5", False),
        ("1.0", False),
        ("1e999", False),
        ("0e0", False),
    ],
)
def test_numeric_variant_controls_default_overlay_source(tmp_path, number, fallback):
    (tmp_path / "plugin.json").write_text(json.dumps({"$schema": SCHEMA, "name": "sample"}))
    (tmp_path / "mcp.json").write_text(
        json.dumps(
            {
                "$schema": MCP_SCHEMA,
                "mcpServers": {"docs": {"type": "stdio", "command": "portable"}},
            }
        )
    )
    overlay = tmp_path / ".codex-plugin/plugin.json"
    overlay.parent.mkdir()
    overlay.write_text('{"mcpServers":' + number + "}")
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"docs": {"command": "ignored", "env_vars": ["FIXTURE_TOKEN"]}})
    )
    manager = PluginManager.discover_and_load(
        roots=(tmp_path,), disabled=frozenset(), registry=ToolRegistry()
    )
    assert [p.manifest.name for p in manager.plugins] == ["sample"]
    assert len(manager.mcp_registrations) == 1
    settings = manager.mcp_registrations[0].settings
    assert settings.command == "portable"
    assert settings.env_vars == (("FIXTURE_TOKEN",) if fallback else ())


@pytest.mark.parametrize("mode", ["direct", "native", "compatible", "code_mode"])
@pytest.mark.parametrize(("number", "fallback"), [("1", True), ("1.5", False)])
def test_numeric_overlay_selection_reaches_child_and_observation(
    tmp_path, monkeypatch, mode, number, fallback
):
    async def scenario():
        root = package(tmp_path, overlay=False)
        script = Path(__file__).resolve().parents[1] / "fixtures/agent_plugin_mcp_server.py"
        report = tmp_path / "process-report.json"
        token = "CORKI_PLUGIN_FIXTURE_TOKEN"
        monkeypatch.setenv(token, "inherited-fixture")
        (root / "mcp.json").write_text(
            json.dumps(
                {
                    "$schema": MCP_SCHEMA,
                    "mcpServers": {
                        "docs": {
                            "type": "stdio",
                            "command": Path(sys.executable).name,
                            "args": [str(script), str(report)],
                            "env": {
                                "PATH": str(Path(sys.executable).parent),
                                token: "${" + token + "}",
                            },
                        }
                    },
                }
            )
        )
        overlay = root / ".codex-plugin/plugin.json"
        overlay.parent.mkdir()
        overlay.write_text('{"mcpServers":' + number + "}")
        (root / ".mcp.json").write_text(
            json.dumps({"docs": {"command": "must-not-run", "env_vars": [token]}})
        )
        carrier = Carrier()
        runtime = await runtime_for(tmp_path, monkeypatch, carrier, mode)
        process = None
        try:
            await runtime._ensure_ready()
            await runtime._mcp_manager.refresh_if_dirty()
            assert set(runtime._mcp_manager._clients_by_name) == {"docs"}
            process = runtime._mcp_manager._clients_by_name["docs"].client._process
            events = [event async for event in runtime.stream("needle plugin env")]
            assert isinstance(events[-1], TurnCompleted)
            actual = json.loads(report.read_text())
            expected = "inherited-fixture" if fallback else "${" + token + "}"
            assert actual["calls"] == 1
            assert actual["env"][token] == expected
            results = [
                item
                for item in runtime._model.requests[-1].items
                if isinstance(item, ToolResultItem)
            ]
            assert expected in repr(results)
            if mode in ("native", "compatible"):
                assert any(item.tool_name == "tool_search" for item in results)
        finally:
            await runtime.aclose()
        assert process is not None and process.returncode is not None
        assert carrier.requests == []

    asyncio.run(scenario())
