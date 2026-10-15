"""Only expected call-admission failures become MCP result values."""

import asyncio
from dataclasses import replace

import pytest
from test_mcp import FakeMCPClient

from corki.config import MCPServerSettings
from corki.mcp.admission import MCPAdmissionError, MCPDisabledError, MCPUnavailableError
from corki.mcp.manager import MCPManager
from corki.mcp.tools import MCPTool
from corki.protocol.ids import ToolCallId
from corki.protocol.tools import ToolCall
from corki.tools import ToolContext, ToolRegistry


@pytest.mark.parametrize("error_type", [KeyError, ValueError, RuntimeError, asyncio.CancelledError])
def test_untyped_program_errors_and_caller_cancellation_are_not_mcp_values(tmp_path, error_type):
    async def scenario():
        error = error_type("handler defect or caller cancellation")

        async def fail(*args, **kwargs):
            raise error

        tool = MCPTool("docs", {"name": "search"}, None, call_router=fail)
        with pytest.raises(error_type) as caught:
            await tool.execute(
                ToolCall(ToolCallId("guard"), tool.spec.name, {}), ToolContext(cwd=tmp_path)
            )
        assert caught.value is error

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "error_type,host_type", [(MCPUnavailableError, KeyError), (MCPDisabledError, ValueError)]
)
def test_typed_admission_errors_keep_host_compatibility_and_normalized_results(
    tmp_path, error_type, host_type
):
    async def scenario():
        error = error_type("not available to the model")
        assert isinstance(error, host_type) and isinstance(error, MCPAdmissionError)

        async def fail(*args, **kwargs):
            raise error

        tool = MCPTool("docs", {"name": "search"}, None, call_router=fail)
        result = await tool.execute(
            ToolCall(ToolCallId("typed"), tool.spec.name, {}), ToolContext(cwd=tmp_path)
        )
        assert result.is_error
        assert result.code_mode_output.value["isError"] is True
        assert "not available to the model" in result.content
        assert "_meta" not in result.code_mode_output.value

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["removed", "disabled", "missing"])
def test_manager_rejects_before_approval_external_context_output_policy_or_rpc(monkeypatch, change):
    async def scenario():
        settings = MCPServerSettings("docs", "http", url="https://fixture.invalid")
        client = FakeMCPClient(settings)
        callbacks = []

        async def approve(*args):
            callbacks.append("approval")

        async def mark():
            callbacks.append("external")

        async def invoke(*args):
            callbacks.append("rpc")
            raise AssertionError("unavailable tool reached RPC")

        monkeypatch.setattr(client, "call_tool", invoke)
        monkeypatch.setattr("corki.mcp.manager.create_client", lambda _: client)
        manager = MCPManager((settings,), ToolRegistry())
        monkeypatch.setattr(manager._approvals, "check", approve)
        try:
            await manager.start()
            if change == "removed":
                manager.request_reconcile(())
            elif change == "disabled":
                manager.request_reconcile((replace(settings, disabled_tools=("search",)),))
            error_type = MCPDisabledError if change == "disabled" else MCPUnavailableError
            with pytest.raises(error_type):
                await manager.call_tool(
                    "docs",
                    "missing" if change == "missing" else "search",
                    {},
                    on_external_context=mark,
                    on_output_token_limit=lambda _: callbacks.append("output"),
                )
            assert callbacks == []
        finally:
            await manager.aclose()
        assert client.closed

    asyncio.run(scenario())
