"""Host MCP authority is applied before transport construction and on refreshed admission."""

import asyncio
import json
from dataclasses import replace

import pytest
from test_mcp_tool_approval import Client, Model

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import MCPProtocolError
from corki.models import ModelCompleted
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.tools import ToolRegistry


class FinalModel:
    def __init__(self):
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        yield ModelCompleted(
            (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
        )

    async def aclose(self):
        pass


def make_runtime(
    tmp_path,
    monkeypatch,
    *,
    requirements,
    servers=(),
    model=None,
    plugins=(),
    mode="direct",
    approval="never",
):
    clients = []

    def factory(settings):
        client = Client(settings)
        clients.append(client)
        return client

    monkeypatch.setattr("corki.mcp.manager.create_client", factory)
    actual_model = model or FinalModel()
    runtime = LangGraphRuntime.create(
        settings=CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            mcp_servers=servers,
            model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
            plugin_dirs=plugins,
            tool_search_mode=mode if mode in ("native", "compatible") else "disabled",
            tool_mode="code_mode" if mode == "code_mode" else "direct",
            api_mode="responses",
            mcp_approval_policy=approval,
        ),
        model=actual_model,
        registry=ToolRegistry(),
        database_path=tmp_path / "history.db",
        home_path=tmp_path / "home",
        mcp_requirements=requirements,
    )
    return runtime, clients, actual_model


@pytest.mark.parametrize(
    "identity", [None, {"url": "https://wrong.invalid"}, {"command": "helper"}]
)
@pytest.mark.parametrize("required", [False, True])
def test_denied_server_never_constructs_transport_or_header_helper(
    tmp_path, monkeypatch, identity, required
):
    async def scenario():
        rules = {} if identity is None else {"docs": {"identity": identity}}
        server = MCPServerSettings(
            "docs",
            "http",
            url="https://allowed.invalid",
            http_headers_helper="must-not-run",
            required=required,
        )
        runtime, clients, model = make_runtime(
            tmp_path,
            monkeypatch,
            requirements={"mcp_servers": rules},
            servers=(server,),
        )
        try:
            events = [e async for e in runtime.stream("hello")]
            assert isinstance(events[-1], TurnCompleted)
            assert clients == [], "denied transport/helper must never be constructed"
            assert not any(t.name.startswith("mcp__docs") for t in model.requests[0].tools)
            assert "managed requirements" in repr(runtime._mcp_manager.warnings)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "native", "compatible", "code_mode"])
def test_allowed_identity_reaches_real_tool_loop_and_closes_client(tmp_path, monkeypatch, mode):
    async def scenario():
        runtime, clients, model = make_runtime(
            tmp_path,
            monkeypatch,
            mode=mode,
            model=Model(mode),
            requirements={
                "mcp_servers": {"docs": {"identity": {"url": "https://allowed.invalid"}}}
            },
            servers=(MCPServerSettings("docs", "http", url="https://allowed.invalid"),),
        )
        try:
            events = [e async for e in runtime.stream("needle")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(clients) == 1
            assert clients[0].calls == [("write", {"value": 1})]
            results = [i for i in model.requests[-1].items if isinstance(i, ToolResultItem)]
            assert "remote effect" in repr(results)
        finally:
            await runtime.aclose()
        assert clients[0].closed

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "native", "compatible", "code_mode"])
@pytest.mark.parametrize("force", [False, True])
def test_changed_identity_after_exposure_is_denied_before_effect(
    tmp_path, monkeypatch, mode, force
):
    async def scenario():
        original = MCPServerSettings("docs", "http", url="https://allowed.invalid")
        replacement = replace(original, url="https://denied.invalid", http_headers_helper="never")

        class ChangingModel(Model):
            async def stream(self, request):
                async for event in super().stream(request):
                    if any(
                        isinstance(i, ToolCallItem) and i.call.name != "tool_search"
                        for i in event.items
                    ):
                        update = (
                            runtime.request_mcp_refresh if force else runtime.request_mcp_reconcile
                        )
                        update((replacement,))
                    yield event

        runtime, clients, model = make_runtime(
            tmp_path,
            monkeypatch,
            mode=mode,
            model=ChangingModel(mode),
            servers=(original,),
            requirements={"mcp_servers": {"docs": {"identity": {"url": original.url}}}},
        )
        try:
            events = [e async for e in runtime.stream("needle")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(clients) == 1, "denied replacement must not construct a client or helper"
            assert clients[0].calls == []
            results = [i for i in model.requests[-1].items if isinstance(i, ToolResultItem)]
            assert "disabled by managed requirements" in repr(results)
            assert not any(t.name.startswith("mcp__docs") for t in model.requests[-1].tools)
        finally:
            await runtime.aclose()
        assert clients[0].closed

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "rule_name,allowed", [("raw__docs", True), ("pack__age__raw__docs", False)]
)
def test_plugin_policy_uses_raw_identity_not_ambiguous_route(
    tmp_path, monkeypatch, rule_name, allowed
):
    plugin = tmp_path / "package"
    manifest = plugin / ".codex-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"name": "pack__age"}))
    (plugin / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"raw__docs": {"url": "https://allowed.invalid"}}})
    )

    async def scenario():
        runtime, clients, model = make_runtime(
            tmp_path,
            monkeypatch,
            plugins=(plugin,),
            requirements={
                "plugins": {
                    "pack__age": {
                        "mcp_servers": {rule_name: {"identity": {"url": "https://allowed.invalid"}}}
                    }
                }
            },
        )
        try:
            events = [e async for e in runtime.stream("hello")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(clients) == int(allowed)
            assert (
                any(t.name.startswith("mcp__raw__docs") for t in model.requests[0].tools) == allowed
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_host_input_mutation_cannot_widen_authority(tmp_path, monkeypatch):
    async def scenario():
        identity = {"url": "https://original.invalid"}
        rules = {"mcp_servers": {"docs": {"identity": identity}}}
        runtime, clients, _ = make_runtime(
            tmp_path,
            monkeypatch,
            requirements=rules,
            servers=(MCPServerSettings("docs", "http", url="https://denied.invalid"),),
        )
        identity["url"] = "https://denied.invalid"
        rules.clear()
        try:
            events = [e async for e in runtime.stream("hello")]
            assert isinstance(events[-1], TurnCompleted)
            assert clients == []
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "requirements",
    [
        {"allowed_approval_policies": ["never"]},
        {"mcp_servers": []},
        {"mcp_servers": {"docs": {"identity": {"command": {"executable": "server"}}}}},
        {"plugins": {"package": {"enabled": True}}},
    ],
)
def test_invalid_authority_fails_before_plugin_or_database_startup(
    tmp_path, monkeypatch, requirements
):
    def forbidden(*args, **kwargs):
        pytest.fail("resources were allocated before validating host authority")

    monkeypatch.setattr("corki.core.runtime.ProcessManager", forbidden)
    monkeypatch.setattr("corki.core.runtime.SQLiteSessionRepository", forbidden)
    monkeypatch.setattr("corki.core.runtime.PluginManager.discover_and_load", forbidden)
    with pytest.raises(ValueError):
        make_runtime(tmp_path, monkeypatch, requirements=requirements)
    assert not (tmp_path / "history.db").exists()


def test_admitted_call_keeps_exact_connection_while_new_admission_is_denied(tmp_path, monkeypatch):
    async def scenario():
        original = MCPServerSettings(
            "docs", "http", url="https://allowed.invalid", default_tools_approval_mode="prompt"
        )
        runtime, clients, model = make_runtime(
            tmp_path,
            monkeypatch,
            servers=(original,),
            model=Model(),
            approval="on-request",
            requirements={"mcp_servers": {"docs": {"identity": {"url": original.url}}}},
        )
        entered, release = asyncio.Event(), asyncio.Event()

        async def host(request):
            entered.set()
            await release.wait()
            runtime.respond_mcp_elicitation(request.server_name, request.request_id, "accept")

        async def consume():
            return [e async for e in runtime.stream("needle")]

        runtime.set_mcp_elicitation_handler(host)
        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(entered.wait(), 5)
            assert clients[0].calls == []
            runtime.request_mcp_reconcile((replace(original, url="https://denied.invalid"),))
            await runtime._mcp_manager.refresh_if_dirty()
            assert len(clients) == 1 and not clients[0].closed
            with pytest.raises(MCPProtocolError, match="managed requirements"):
                await runtime._mcp_manager.call_tool("docs", "write", {"new": True})
            release.set()
            events = await asyncio.wait_for(task, 5)
            assert isinstance(events[-1], TurnCompleted)
            assert clients[0].calls == [("write", {"value": 1})]
            assert "remote effect" in repr(model.requests[-1].items)
            assert "https://denied.invalid" not in repr(model.requests[-1].items)
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()
        assert clients[0].closed

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel", [False, True])
def test_failed_publication_keeps_generation_then_retries_restriction(
    tmp_path, monkeypatch, cancel
):
    async def scenario():
        original = MCPServerSettings("docs", "http", url="https://allowed.invalid")
        runtime, clients, _ = make_runtime(
            tmp_path,
            monkeypatch,
            servers=(original,),
            requirements={"mcp_servers": {"docs": {"identity": {"url": original.url}}}},
        )
        manager = runtime._mcp_manager
        try:
            await manager.start()
            names = manager.tool_names
            catalog = manager.catalog
            connection = manager._clients_by_name["docs"]
            runtime.request_mcp_reconcile((replace(original, url="https://denied.invalid"),))
            error = asyncio.CancelledError if cancel else RuntimeError

            def fail(*args, **kwargs):
                raise error("publication interrupted")

            with monkeypatch.context() as patch:
                patch.setattr(manager._registry, "replace_owned", fail)
                with pytest.raises(error):
                    await manager.refresh_if_dirty()
            assert manager.refresh_pending
            assert manager.tool_names == names
            assert manager.catalog.servers == catalog.servers
            assert manager._client("docs") is connection
            assert not clients[0].closed
            # No call can use the unpublished old view: admission retries pending
            # reconciliation first, then returns a denial instead of a remote effect.
            with pytest.raises(MCPProtocolError, match="managed requirements"):
                await manager.call_tool("docs", "write", {})
            assert manager.tool_names == ()
            assert not manager.catalog.servers[0].settings.enabled
            assert len(clients) == 1 and clients[0].calls == []
            assert not manager.refresh_pending
        finally:
            await runtime.aclose()
        assert clients[0].closed

    asyncio.run(scenario())
