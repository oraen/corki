"""Legacy hook metadata failures must respect Agent extension precedence."""

import asyncio
import json

import pytest
from test_mcp_agent_plugin import package, runtime_for
from test_mcp_bound_http import Carrier

from corki.protocol.events import TurnCompleted
from corki.protocol.items import ToolResultItem


@pytest.mark.parametrize("mode", ["direct", "native", "compatible", "code_mode"])
@pytest.mark.parametrize("field", ["hooks", "mcpServers"])
@pytest.mark.parametrize("extension", [False, True])
@pytest.mark.parametrize("valid_hooks", [False, True])
def test_hook_overlay_failure_and_precedence_reach_runtime(
    tmp_path, monkeypatch, mode, field, extension, valid_hooks
):
    async def scenario():
        root = package(tmp_path, overlay=False)
        if extension:
            manifest = root / "plugin.json"
            content = json.loads(manifest.read_text())
            content["extensions"] = {"com.openai": {}}
            manifest.write_text(json.dumps(content))
        bad = {"$serde_json::private::RawValue": "not-json"}
        hooks = {"hooks": {"future": bad}} if valid_hooks else bad
        if field == "mcpServers":
            hooks = {} if valid_hooks else [bad]
        overlay = root / ".codex-plugin/plugin.json"
        overlay.parent.mkdir()
        overlay.write_text(json.dumps({field: hooks}))
        carrier = Carrier()
        runtime = await runtime_for(tmp_path, monkeypatch, carrier, mode)
        try:
            await runtime._ensure_ready()
            await runtime._mcp_manager.refresh_if_dirty()
            if not extension and not valid_hooks:
                assert runtime._plugin_manager.plugins == ()
                assert runtime._plugin_manager.warnings
                assert carrier.requests == []
            else:
                assert [p.manifest.name for p in runtime._plugin_manager.plugins] == ["sample"]
                assert bool(runtime._plugin_manager.warnings) == (not valid_hooks)
                events = [event async for event in runtime.stream("needle")]
                assert isinstance(events[-1], TurnCompleted)
                assert len(carrier.calls) == 1
                results = [
                    item
                    for item in runtime._model.requests[-1].items
                    if isinstance(item, ToolResultItem)
                ]
                assert "first remote effect" in repr(results)
                if mode in ("native", "compatible"):
                    assert any(item.tool_name == "tool_search" for item in results)
        finally:
            await runtime.aclose()
        assert all(body.closed for body in carrier.bodies)

    asyncio.run(scenario())
