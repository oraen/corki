import asyncio
from types import SimpleNamespace

import pytest

from corki.cli.mcp_inventory import MCPInventory


@pytest.mark.parametrize("outcome", ["success", "failure", "cancel", "cancel_before_start"])
def test_transient_loading_clears_without_changing_other_ui_state(tmp_path, outcome):
    from corki.cli.terminal import TerminalUI
    from corki.config import CorkiSettings

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        async def catalog():
            started.set()
            await release.wait()
            if outcome == "failure":
                raise ValueError("secret")
            return ()

        ui = TerminalUI(CorkiSettings(tmp_path), tmp_path / "history")
        ui._reasoning_active = True
        ui._reasoning_header = "Working on request"
        toolbar = ui._toolbar()
        inventory = MCPInventory(SimpleNamespace(mcp_tool_catalog=catalog), ui)
        inventory.start()
        try:
            assert ui._mcp_loading is True
            assert ui._toolbar() == toolbar
            if outcome != "cancel_before_start":
                await started.wait()
            if outcome in ("success", "failure"):
                release.set()
                await asyncio.wait_for(inventory.task, 1)
            else:
                await inventory.aclose()
            assert ui._mcp_loading is False
            assert ui._toolbar() == toolbar
            assert not any("Loading MCP" in str(entry) for entry in ui._transcript.calls)
        finally:
            await inventory.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [False, True])
def test_inventory_queries_real_provider_once_and_recovers(failure):
    async def scenario():
        ready, release = asyncio.Event(), asyncio.Event()
        notices = []
        calls = []

        async def catalog():
            calls.append(True)
            ready.set()
            await release.wait()
            if failure:
                raise ValueError("secret-token")
            return (SimpleNamespace(server_name="local", name="mcp__local::read"),)

        ui = SimpleNamespace(show_notice=notices.append)
        inventory = MCPInventory(SimpleNamespace(mcp_tool_catalog=catalog), ui)
        inventory.start()
        await ready.wait()
        inventory.start()
        release.set()
        await asyncio.wait_for(inventory.task, 1)
        assert calls == [True]
        assert "secret-token" not in "\n".join(notices)
        assert ("failed" if failure else "mcp__local::read") in notices[-1]
        inventory.start()
        await asyncio.wait_for(inventory.task, 1)
        assert calls == [True, True]
        await inventory.aclose()

    asyncio.run(scenario())


def test_inventory_shutdown_cancels_and_joins_pending_query():
    async def scenario():
        started, closed = asyncio.Event(), asyncio.Event()
        notices = []

        async def catalog():
            started.set()
            try:
                await asyncio.Future()
            finally:
                closed.set()

        inventory = MCPInventory(
            SimpleNamespace(mcp_tool_catalog=catalog), SimpleNamespace(show_notice=notices.append)
        )
        inventory.start()
        await started.wait()
        await inventory.aclose()
        assert closed.is_set() and inventory.task.done()
        assert len(notices) == 1

    asyncio.run(scenario())
