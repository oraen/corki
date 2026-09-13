"""Explicit ownership of resource Steps, admitted calls, and startup fallback."""

import asyncio

import pytest

from corki.config import MCPServerSettings
from corki.core.step_tools import StepToolState
from corki.mcp.client import MCPClient, MCPProtocolError
from corki.mcp.manager import MCPManager
from corki.tools import ToolRegistry


class Client(MCPClient):
    def __init__(self, settings, value):
        super().__init__(settings)
        self.value = value
        self.closed = asyncio.Event()
        self.started = asyncio.Event()
        self.start_gate = None
        self.close_gate = None
        self.closing = asyncio.Event()

    async def start(self):
        self.started.set()
        if self.start_gate is not None:
            await self.start_gate.wait()

    async def list_tools(self):
        return ()

    async def _exchange(self, message):
        raise AssertionError(message)

    async def _send_notification(self, message):
        raise AssertionError(message)

    async def list_resources(self, cursor=None):
        assert not self.closed.is_set()
        return {"resources": [{"uri": "fixture:value", "name": self.value}]}

    async def aclose(self):
        self.closing.set()
        if self.close_gate is not None:
            await self.close_gate.wait()
        self.closed.set()


def make_manager(monkeypatch):
    settings = MCPServerSettings("docs", "http", url="https://docs.invalid")
    old, new = Client(settings, "old"), Client(settings, "new")
    clients = iter((old, new))
    monkeypatch.setattr("corki.mcp.manager.create_client", lambda _: next(clients))
    registry = ToolRegistry()
    return MCPManager((settings,), registry), registry, old, new


@pytest.mark.parametrize("finish", ["success", "cancel"])
def test_admitted_resource_call_keeps_binding_after_step_releases_it(monkeypatch, finish):
    async def scenario():
        manager, _, old, new = make_manager(monkeypatch)
        await manager.start()
        binding = manager.capture_resource_binding()
        entered, gate = asyncio.Event(), asyncio.Event()

        async def call():
            with binding.lease():
                entered.set()
                await gate.wait()
                return await binding.list_capability("resources", "docs")

        task = asyncio.create_task(call())
        try:
            await entered.wait()
            manager.request_refresh()
            await manager.refresh_if_dirty()
            binding.release()
            assert not binding.closed.done() and not old.closed.is_set()
            if finish == "success":
                gate.set()
                assert await task == {
                    "server": "docs",
                    "resources": [{"server": "docs", "uri": "fixture:value", "name": "old"}],
                }
            else:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            await binding.closed
            assert old.closed.is_set() and not new.closed.is_set()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await manager.aclose()
        assert new.closed.is_set()

    asyncio.run(scenario())


def test_binding_after_failed_refresh_still_owns_previous_publication(monkeypatch):
    async def scenario():
        manager, registry, old, _ = make_manager(monkeypatch)
        await manager.start()
        manager.request_refresh()
        with monkeypatch.context() as patch:

            def fail(*args):
                raise ValueError("fixture publication failure")

            patch.setattr(registry, "replace_owned", fail)
            with pytest.raises(ValueError, match="publication"):
                await manager.refresh_if_dirty()
        assert manager._preparation is None and not old.closed.is_set()
        binding = manager.capture_resource_binding()
        try:
            manager.request_refresh(())
            await manager.refresh_if_dirty()
            with binding.lease():
                result = await binding.list_capability("resources", "docs")
            assert result["resources"][0]["name"] == "old"
        finally:
            binding.release()
            await binding.closed
            await manager.aclose()
        assert old.closed.is_set()

    asyncio.run(scenario())


def test_repeated_cancel_of_step_cleanup_still_joins_old_transport(monkeypatch):
    async def scenario():
        manager, registry, old, _ = make_manager(monkeypatch)
        await manager.start()
        state = StepToolState(capture_resources=manager.capture_resource_binding)
        state.bind(registry.snapshot())
        manager.request_refresh()
        await manager.refresh_if_dirty()
        old.close_gate = asyncio.Event()
        cleanup = asyncio.create_task(state.aclose())
        try:
            await old.closing.wait()
            cleanup.cancel()
            await asyncio.sleep(0)
            cleanup.cancel()
            await asyncio.sleep(0)
            assert not cleanup.done() and not old.closed.is_set()
            old.close_gate.set()
            with pytest.raises(asyncio.CancelledError):
                await cleanup
            assert old.closed.is_set()
            await state.aclose()
        finally:
            old.close_gate.set()
            await asyncio.gather(cleanup, return_exceptions=True)
            await manager.aclose()

    asyncio.run(scenario())


def test_cancelled_named_startup_is_tool_error_not_caller_cancellation(monkeypatch):
    async def scenario():
        manager, _, old, _ = make_manager(monkeypatch)
        old.start_gate = asyncio.Event()
        await manager.publish_pending()
        await old.started.wait()
        binding = manager.capture_resource_binding()
        manager.cancel_startup()
        try:
            with binding.lease(), pytest.raises(MCPProtocolError, match="startup.*cancelled"):
                await binding.list_capability("resources", "docs")
        finally:
            binding.release()
            await binding.closed
            await manager.aclose()

    asyncio.run(scenario())
