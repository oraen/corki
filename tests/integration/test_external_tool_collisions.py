"""External collisions are selected-plan facts, not whole-transport failures."""

import asyncio
import json

import pytest
from test_code_mode import request_call

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import MCPClient
from corki.models import ModelCompleted
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted, TurnFailed
from corki.protocol.items import ToolResultItem
from corki.protocol.tools import ToolResult, ToolSpec
from corki.tools import ToolRegistry, ToolSource


@pytest.mark.parametrize("strict", [False, True])
def test_mcp_collision_keeps_same_server_healthy_tool_and_core_handler(
    tmp_path, monkeypatch, strict
):
    async def scenario():
        calls, requests, closed = [], [], []

        class Client(MCPClient):
            async def start(self):
                pass

            async def list_tools(self):
                return tuple(
                    {"name": n, "description": "proof", "inputSchema": {"type": "object"}}
                    for n in ("conflict", "healthy")
                )

            async def call_tool(self, name, arguments):
                assert not closed
                calls.append(name)
                return {"content": [{"type": "text", "text": "REMOTE_HEALTHY"}]}

            async def _exchange(self, message):
                raise AssertionError(message)

            async def _send_notification(self, message):
                raise AssertionError(message)

            async def aclose(self):
                closed.append(True)

        class Core:
            spec = ToolSpec("mcp__docs::conflict", "core wins", {})

            async def execute(self, call, context):
                calls.append("core")
                return ToolResult(call.id, call.name, "CORE_RESULT")

        class Model:
            async def stream(self, request):
                requests.append(request)
                assert "mcp__docs::healthy" in {s.name for s in request.tools}
                if len(requests) == 1:
                    yield request_call(request, "mcp__docs::healthy", {})
                elif len(requests) == 2:
                    yield request_call(request, "mcp__docs::conflict", {})
                else:
                    results = [i.content for i in request.items if isinstance(i, ToolResultItem)]
                    assert any("REMOTE_HEALTHY" in s for s in results) and "CORE_RESULT" in results
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        server = MCPServerSettings("docs", "http", url="https://fixture.invalid")
        client = Client(server)
        monkeypatch.setattr("corki.mcp.manager.create_client", lambda _: client)
        registry = ToolRegistry()
        core = Core()
        registry.register(core)
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                mcp_servers=(server,),
                tool_search_mode="disabled",
                error_on_tool_collisions=strict,
            ),
            registry=registry,
            model=Model(),
            database_path=tmp_path / "s.db",
        )
        try:
            events = [e async for e in runtime.stream("use both tools")]
            assert isinstance(events[-1], TurnFailed if strict else TurnCompleted), events[-1]
            assert not closed
            assert registry.get(core.spec.name) is core
            if strict:
                assert "tool collision: mcp__docs.conflict" in events[-1].error
                assert not calls and not requests
            else:
                assert calls == ["healthy", "core"]
        finally:
            await runtime.aclose()
        assert closed

    asyncio.run(scenario())


@pytest.mark.parametrize("search_mode", ["disabled", "compatible", "native"])
@pytest.mark.parametrize("failed_publication", [False, True])
def test_real_plugin_mcp_and_dynamic_priority_refresh_and_cold_history(
    tmp_path, monkeypatch, search_mode, failed_publication
):
    async def scenario():
        name = "plugin__sample__echo"
        plugin = tmp_path / "plugins" / "sample"
        manifest = plugin / ".codex-plugin" / "plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"name": "sample", "entrypoint": "plugin.py:register"}))
        (plugin / "plugin.py").write_text(
            "def register(api):\n"
            "    def run(args, ctx):\n"
            "        (ctx.cwd / 'extension-ran').write_text('yes')\n"
            "        return 'EXTENSION_RESULT'\n"
            "    api.register_tool(name='echo', description='proof', parameters={}, handler=run)\n"
        )
        requests, executed, closed = [], [], []
        expected = ["MCP_RESULT"]
        publication = {}

        class Client(MCPClient):
            async def start(self):
                pass

            async def list_tools(self):
                return ({"name": name, "description": "proof", "inputSchema": {"type": "object"}},)

            async def call_tool(self, remote, arguments):
                executed.append("MCP")
                return {"content": [{"type": "text", "text": "MCP_RESULT"}]}

            async def _exchange(self, message):
                raise AssertionError(message)

            async def _send_notification(self, message):
                raise AssertionError(message)

            async def aclose(self):
                closed.append(True)

        class Dynamic:
            spec = ToolSpec(name, "dynamic proof", {})

            async def execute(self, call, context):
                executed.append("DYNAMIC")
                return ToolResult(call.id, call.name, "DYNAMIC_RESULT")

        class Model:
            async def stream(self, request):
                requests.append(request)
                results = [
                    i.content
                    for i in request.items
                    if isinstance(i, ToolResultItem)
                    and i.tool_name == name
                    and i.turn_id == request.items[-1].turn_id
                ]
                if results:
                    assert any(expected[0] in result for result in results)
                    yield ModelCompleted(())
                elif name not in {s.name for s in request.tools} and not any(
                    spec.name == name
                    for item in request.items
                    if isinstance(item, ToolResultItem)
                    for spec in item.discovered_tools
                ):
                    yield request_call(request, "tool_search", {"query": "proof"})
                else:
                    yield request_call(request, name, {})

            async def aclose(self):
                pass

        server = MCPServerSettings("functions", "http", url="https://fixture.invalid")
        monkeypatch.setattr("corki.mcp.manager.create_client", Client)

        async def create(thread=None):
            registry = ToolRegistry()
            owner = registry.create_owner(source=ToolSource.DYNAMIC)
            registry.replace_owned(owner, (Dynamic(),))
            publication.update(registry=registry, owner=owner)
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    tmp_path,
                    skills_enabled=False,
                    plugin_dirs=(tmp_path / "plugins",),
                    non_prefixed_mcp_tool_names=True,
                    tool_search_mode=search_mode,
                    api_mode="responses" if search_mode == "native" else "chat_completions",
                    model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
                    mcp_servers=(server,) if thread is None else (),
                ),
                registry=registry,
                model=Model(),
                database_path=tmp_path / "s.db",
                thread_id=thread,
                load_plugins=thread is None,
            )

        runtime = await create()
        try:
            first = [e async for e in runtime.stream("call MCP winner")]
            assert isinstance(first[-1], TurnCompleted), first[-1]
            assert executed == ["MCP"] and not (tmp_path / "extension-ran").exists()
            runtime.request_mcp_refresh(())
            expected[0] = "EXTENSION_RESULT"
            second = [e async for e in runtime.stream("call extension fallback")]
            assert isinstance(second[-1], TurnCompleted), second[-1]
            assert (tmp_path / "extension-ran").read_text() == "yes"
            assert executed == ["MCP"] and closed
            thread = runtime.thread_id
            await runtime.aclose()
            runtime = await create(thread)
            expected[0] = "DYNAMIC_RESULT"
            third = [e async for e in runtime.stream("call dynamic fallback")]
            assert isinstance(third[-1], TurnCompleted), third[-1]
            assert executed == ["MCP", "DYNAMIC"]
            if failed_publication:
                registry, owner = publication["registry"], publication["owner"]
                before = registry.snapshot()

                class Broken:
                    @property
                    def spec(self):
                        raise ValueError("injected dynamic definition capture failure")

                with pytest.raises(ValueError, match="dynamic definition capture failure"):
                    registry.replace_owned(owner, (Dynamic(), Broken()))
                assert registry.snapshot() is before
                assert registry.get(name) is before.get(name)
                assert registry.owned_names(owner) == frozenset({name})
                fourth = [e async for e in runtime.stream("call after failed publication")]
                assert isinstance(fourth[-1], TurnCompleted), fourth[-1]
                assert executed == ["MCP", "DYNAMIC", "DYNAMIC"]
            history = str([i.content for i in requests[-1].items if isinstance(i, ToolResultItem)])
            assert all(
                result in history for result in ("MCP_RESULT", "EXTENSION_RESULT", "DYNAMIC_RESULT")
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
