"""Owned approval writes cannot escape revision authority during cancellation."""

import asyncio
import threading
import tomllib

import pytest
from test_approval_reload import Client

from corki.config import MCPServerSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.config.toml_edits import set_config_value
from corki.mcp.approval_persistence import MCPApprovalPersistence, approval_settings
from corki.mcp.catalog import MCPCatalogSource
from corki.mcp.client import MCPProtocolError
from corki.mcp.manager import MCPManager
from corki.tools import ToolRegistry


@pytest.mark.parametrize("kind", ["plugin", "selected_plugin"])
def test_plugin_policy_does_not_borrow_a_same_named_config_declaration(kind):
    settings = MCPServerSettings("docs", "http", url="https://fixture.invalid")
    document = {
        "mcp": {"servers": {"docs": {"default_tools_approval_mode": "approve"}}},
        "plugins": {
            "fixture": {"mcp_servers": {"docs": {"default_tools_approval_mode": "prompt"}}}
        },
    }
    actual = approval_settings(settings, document, MCPCatalogSource(kind, "fixture"))
    assert actual.default_tools_approval_mode == "prompt"


@pytest.mark.parametrize("declared", ["auto", "writes", "prompt", "approve"])
@pytest.mark.parametrize("requested", ["auto", "writes", "prompt", "approve"])
def test_selected_plugin_policy_intersection_and_installed_plugin_override(declared, requested):
    matrix = {
        "auto": ("auto", "prompt", "prompt", "auto"),
        "writes": ("prompt", "writes", "prompt", "writes"),
        "prompt": ("prompt", "prompt", "prompt", "prompt"),
        "approve": ("auto", "writes", "prompt", "approve"),
    }
    settings = MCPServerSettings(
        "docs",
        "http",
        url="https://fixture.invalid",
        default_tools_approval_mode=declared,
        tool_approval_modes=(("read", declared),),
    )
    document = {
        "plugins": {
            "fixture": {"mcp_servers": {"docs": {"default_tools_approval_mode": requested}}}
        }
    }
    selected = approval_settings(settings, document, MCPCatalogSource("selected_plugin", "fixture"))
    expected = matrix[declared][("auto", "writes", "prompt", "approve").index(requested)]
    assert (selected.default_tools_approval_mode, selected.tool_approval_modes) == (
        expected,
        (("read", expected),),
    )
    installed = approval_settings(settings, document, MCPCatalogSource("plugin", "fixture"))
    assert (installed.default_tools_approval_mode, installed.tool_approval_modes) == (
        requested,
        (("read", declared),),
    )


@pytest.mark.parametrize("finish", ["cancel", "shutdown", "stale"])
@pytest.mark.parametrize("name", ["docs", "codex_apps"])
def test_approval_write_lifecycle_preserves_exact_revision(tmp_path, monkeypatch, finish, name):
    async def scenario():
        config = tmp_path / "config.toml"
        original = (
            f'# keep\n[mcp.servers.{name}]\ntransport="http"\nurl="https://fixture.invalid"\n'
        )
        config.write_text(original)
        state = LocalConfigState((ConfigLayer(config, "user", contents=original),))
        persistence = MCPApprovalPersistence(state, tmp_path)
        settings = MCPServerSettings(name, "http", url="https://fixture.invalid")
        client = Client(settings)
        monkeypatch.setattr("corki.mcp.manager.create_client", lambda _: client)
        manager = MCPManager(
            (settings,),
            ToolRegistry(),
            approval_policy="on-request",
            approval_persistence=persistence,
        )
        entered = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        writes, memory = [], []

        def write(*args):
            writes.append(args)
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5), "test must release owned writer"
            set_config_value(*args)

        monkeypatch.setattr("corki.mcp.approval_persistence.set_config_value", write)

        async def revise_catalog():
            # Fault injection at the authority boundary, not an Apps refresh API.
            async with generation.catalog_revision.write() as revision:
                revision.value += 1

        async def host(request):
            if finish == "stale":
                await revise_catalog()
            manager.elicitations.respond(
                request.server_name, request.request_id, "accept", meta={"persist": "always"}
            )

        async def pollute():
            memory.append(True)

        manager.elicitations.handler = host
        task = refresh = closing = None
        try:
            await manager.start()
            generation = manager._preparation
            task = asyncio.create_task(
                manager.call_tool(name, "old", {}, on_external_context=pollute)
            )
            if finish == "stale":
                with pytest.raises(MCPProtocolError, match="catalog changed"):
                    await asyncio.wait_for(task, 2)
                assert not writes and config.read_text() == original
            else:
                await asyncio.wait_for(entered.wait(), 2)
                assert not client.calls and not manager._approvals._session
                if finish == "cancel":
                    task.cancel()
                    refresh = asyncio.create_task(revise_catalog())
                else:
                    closing = asyncio.create_task(manager.aclose())
                await asyncio.sleep(0.01)
                assert not task.done() and generation.catalog_revision.value == 0
                if refresh is not None:
                    assert not refresh.done()
                if closing is not None:
                    assert not closing.done()
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 2)
                if refresh is not None:
                    await asyncio.wait_for(refresh, 2)
                if closing is not None:
                    await asyncio.wait_for(closing, 2)
                assert len(writes) == 1
                assert (
                    tomllib.loads(config.read_text())["mcp"]["servers"][name]["tools"]["old"][
                        "approval_mode"
                    ]
                    == "approve"
                )
            assert not client.calls and not memory and not manager._approvals._session
            assert not manager.elicitations._pending
        finally:
            release.set()
            for pending in (task, refresh, closing):
                if pending is not None:
                    await asyncio.gather(pending, return_exceptions=True)
            await manager.aclose()
        assert client.closed

    asyncio.run(scenario())
