"""Readiness prewarms; actual MCP call preparation owns its selected generation."""

import asyncio
from dataclasses import replace

import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.catalog import MCPCatalog, MCPCatalogSource, MCPRegistration
from corki.mcp.client import MCPClient, MCPProtocolError
from corki.mcp.tool_catalog_cache import CatalogSnapshot, MCPToolCatalogCache
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("operation", ["call", "model_direct", "model_nested"])
@pytest.mark.parametrize("replace_while_waiting", [False, True])
@pytest.mark.parametrize("hold_binding", [False, True])
@pytest.mark.parametrize("fail_initial", [False, True])
@pytest.mark.parametrize("server", ["ordinary", "codex_apps"])
def test_pending_operation_selects_generation_at_call_preparation(
    tmp_path, monkeypatch, operation, replace_while_waiting, hold_binding, fail_initial, server
):
    async def scenario():
        clients, calls = [], []
        model_operation = operation.startswith("model_")
        # Model dispatch first waits for readiness without admitting a call.
        # It selects current authority after that wait and the execution gate.
        # A host call, by contrast, has already selected its execution binding.
        refreshed_dispatch = model_operation and replace_while_waiting
        expected_client = int(refreshed_dispatch)
        expected_failure = fail_initial and not refreshed_dispatch
        startup_entered, startup_release, selected = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )

        class Client(MCPClient):
            async def start(self):
                pass

            async def list_tools(self):
                self.lists += 1
                if self.index == 0 and self.lists == 1:
                    startup_entered.set()
                    await startup_release.wait()
                    if fail_initial:
                        raise MCPProtocolError("original startup failed")
                return (
                    {
                        "name": "echo",
                        "inputSchema": {"type": "object"},
                        "_meta": {"connector_id": "fixture"},
                    },
                )

            async def call_tool(self, name, arguments):
                calls.append(self.index)
                return {"content": [{"type": "text", "text": str(self.index)}]}

            async def aclose(self):
                self.closed = True

            async def _exchange(self, message):
                raise AssertionError(message)

            async def _send_notification(self, message):
                raise AssertionError(message)

        def factory(settings):
            client = Client(settings)
            client.index, client.lists, client.closed = len(clients), 0, False
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)

        class Model:
            calls = 0

            async def stream(self, request):
                assert model_operation, "host operation must not sample a model"
                self.calls += 1
                turn, step = request.items[-1].turn_id, new_step_id()
                if self.calls == 1 and operation == "model_direct":
                    assert f"mcp__{server}::echo" not in {tool.name for tool in request.tools}
                    call = ToolCall(new_tool_call_id(), "tool_search", {"query": f"{server} echo"})
                elif self.calls == (2 if operation == "model_direct" else 1):
                    if operation == "model_direct":
                        assert f"mcp__{server}::echo" in {tool.name for tool in request.tools}
                        call = ToolCall(new_tool_call_id(), f"mcp__{server}::echo", {})
                    else:
                        call = ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments=f"text(await tools.mcp__{server}__echo({{}}));",
                        )
                else:
                    result = [item for item in request.items if isinstance(item, ToolResultItem)][
                        -1
                    ]
                    # MCP error values remain data inside a successful JS cell.
                    assert result.is_error == (expected_failure and operation == "model_direct")
                    if expected_failure and operation == "model_nested":
                        assert '"isError":true' in result.content.replace(" ", "")
                    assert (
                        "original startup failed" if expected_failure else str(expected_client)
                    ) in result.content
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, step),))

            async def aclose(self):
                pass

        settings = MCPServerSettings(server, "http", url="https://old.invalid")
        cache = MCPToolCatalogCache()
        entry = cache.context(settings)
        cached = (
            (
                {
                    "name": "echo",
                    "inputSchema": {"type": "object"},
                    "_meta": {"connector_id": "fixture"},
                },
            )
            if model_operation
            else ()
        )
        entry.publish_if_newest(entry.begin_fetch(), CatalogSnapshot(cached, None))
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                execution_permissions=None,
                mcp_servers=(settings,),
                tool_mode="code_mode_only" if operation == "model_nested" else "direct",
                tool_search_mode="disabled" if operation == "model_nested" else "compatible",
            ),
            mcp_tool_catalog_cache=cache,
            mcp_catalog=MCPCatalog(
                (MCPRegistration(settings, MCPCatalogSource("compatibility", "fixture")),)
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "pending.db",
            home_path=tmp_path / "home",
        )
        binding, task = None, None
        try:
            await runtime._ensure_ready()
            await startup_entered.wait()
            manager = runtime._mcp_manager
            original = manager._preparation
            if hold_binding:
                binding = manager.capture_resource_binding()
            wait = manager._wait_preparation

            async def observe(generation, mode, *args, **kwargs):
                if generation is original and mode == "server":
                    selected.set()
                result = await wait(generation, mode, *args, **kwargs)
                return result

            monkeypatch.setattr(manager, "_wait_preparation", observe)

            async def run_model():
                events = [event async for event in runtime.stream("find and call echo")]
                assert isinstance(events[-1], TurnCompleted)

            task = asyncio.create_task(
                run_model() if model_operation else manager.call_tool(server, "echo", {})
            )
            await asyncio.wait_for(selected.wait(), 1)
            if replace_while_waiting:
                runtime.request_mcp_refresh((replace(settings, url="https://new.invalid"),))
                await runtime._refresh_tools()
                await manager._preparation.tasks[server]
                await runtime._refresh_tools()
                assert manager._preparation is not original and len(clients) == 2
            startup_release.set()
            if expected_failure:
                if model_operation:
                    await asyncio.wait_for(task, 1)
                else:
                    with pytest.raises(MCPProtocolError, match="original startup failed"):
                        await asyncio.wait_for(task, 1)
                assert calls == []
                assert all(client.lists == 1 for client in clients)
                # A new operation may use a separately, explicitly refreshed client.
                if not replace_while_waiting:
                    runtime.request_mcp_refresh((replace(settings, url="https://new.invalid"),))
                await manager.call_tool(server, "echo", {})
                assert calls == [1]
            else:
                await asyncio.wait_for(task, 1)
            if not expected_failure:
                assert calls == [expected_client], "call used the wrong execution generation"
        finally:
            startup_release.set()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            if binding is not None:
                binding.release()
                await binding.closed
            await runtime.aclose()
        assert all(client.closed for client in clients)

    asyncio.run(scenario())
