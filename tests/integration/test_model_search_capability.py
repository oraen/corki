"""Harness discovery ignores legacy native gates while preserving MCP reachability."""

import asyncio
from dataclasses import replace

import pytest
from test_code_mode import request_call
from test_mcp_exposure_surfaces import (
    MASKS,
    install_http_fixture,
)
from test_mcp_exposure_surfaces import (
    test_runtime_server_mask_matrix as check_server_matrix,
)

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, resolve_capabilities
from corki.protocol.events import TurnCompleted
from corki.protocol.items import ContextItem, ToolResultItem
from corki.protocol.tools import ToolExposure, ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize("omitted", MASKS)
@pytest.mark.parametrize("mode", ["direct", "code_mode", "code_mode_only"])
@pytest.mark.parametrize("supported,provider", [(False, True), (True, False)])
def test_disabled_native_gate_composes_with_every_mcp_mask(
    tmp_path, monkeypatch, omitted, mode, supported, provider
):
    check_server_matrix(
        tmp_path,
        monkeypatch,
        omitted,
        mode,
        "native",
        True,
        False,
        model_search=supported,
        provider_namespaces=provider,
    )


@pytest.mark.parametrize("supported", [False, True])
@pytest.mark.parametrize("search", ["native", "compatible", "disabled"])
@pytest.mark.parametrize("provider_namespaces", [False, True])
@pytest.mark.parametrize("native_wire", [False, True])
def test_model_catalog_gate_replans_mcp_and_executes(
    tmp_path, monkeypatch, supported, search, provider_namespaces, native_wire
):
    async def scenario():
        calls, methods, clients, closed = install_http_fixture(monkeypatch)
        config = tmp_path / "config.toml"
        config.write_text(
            '[agent]\nmodel="fixture"\n[skills]\nenabled=false\n'
            "[models.catalog.fixture]\nuse_responses_lite=true\n"
            f"supports_search_tool={str(supported).lower()}\n"
        )
        settings = replace(
            CorkiSettings.for_directory(tmp_path, config_file=config),
            api_mode="responses",
            tool_search_mode=search,
            tool_namespace_mode="native" if native_wire else "compatible",
            turn_metadata_includes_tool_info=True,
            mcp_servers=(MCPServerSettings("fixture", "http", url="https://fixture.invalid/mcp"),),
        )
        enabled = search != "disabled"
        requests = []

        class Model:
            capabilities = replace(
                resolve_capabilities(base_url="https://fixture.invalid/v1", api_mode="responses"),
                namespace_tools=provider_namespaces,
                supports_native_namespaces=native_wire,
            )

            async def stream(self, request):
                requests.append(request)
                names = {s.name for s in request.tools}
                assert ("tool_search" in names) is enabled
                if len(requests) == 1:
                    assert ("mcp__fixture::echo" in names) is (not enabled)
                    catalog = next(
                        i
                        for i in request.items
                        if isinstance(i, ContextItem) and i.key == "extensions.mcp.catalog"
                    )
                    assert ("Deferred sources: fixture" in catalog.content) is enabled
                    assert request.client_metadata is None
                if enabled and len(requests) == 1:
                    yield request_call(request, "tool_search", {"query": "amberproof"})
                elif len(requests) == (2 if enabled else 1):
                    yield request_call(request, "mcp__fixture::echo", {})
                else:
                    assert any(
                        isinstance(i, ToolResultItem) and "ECHO_PROOF" in i.content
                        for i in request.items
                    )
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            registry=ToolRegistry(),
            model=Model(),
            database_path=tmp_path / "s.db",
        )
        try:
            events = [e async for e in runtime.stream("find and echo")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert calls == ["echo"] and len(requests) == (3 if enabled else 2)
            assert len(clients) == methods.count("tools/list") == 1
        finally:
            await runtime.aclose()
        assert closed == ["fixture"]

    asyncio.run(scenario())


@pytest.mark.parametrize("model_name", ["gpt-6-astra", "gpt-5.5"])
def test_bundled_catalog_controls_real_search_and_execution(tmp_path, model_name):
    async def scenario():
        requests, calls = [], []
        nested = model_name == "gpt-6-astra"

        class Probe:
            spec = ToolSpec("probe", "bundleproof", {}, exposure=ToolExposure.DEFERRED)

            async def execute(self, call, context):
                calls.append(call.name)
                return ToolResult(call.id, call.name, "BUNDLE_PROOF")

        class Model:
            async def stream(self, request):
                requests.append(request)
                assert request.model_info.supports_search_tool
                names = {t.name for t in request.tools}
                assert "tool_search" in names and ("exec" in names) is nested
                if len(requests) == 1:
                    yield request_call(
                        request,
                        "exec" if nested else "tool_search",
                        "text(await tools.probe({}));" if nested else {"query": "bundleproof"},
                    )
                elif not nested and len(requests) == 2:
                    yield request_call(request, "probe", {})
                else:
                    assert any(
                        isinstance(i, ToolResultItem) and "BUNDLE_PROOF" in i.content
                        for i in request.items
                    )
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Probe())
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                model=model_name,
                api_mode="responses",
                skills_enabled=False,
                tool_search_mode="native",
                tool_mode="direct",
            ),
            registry=registry,
            model=Model(),
            database_path=tmp_path / "s.db",
        )
        try:
            events = [e async for e in runtime.stream("use bundled catalog")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert calls == ["probe"] and len(requests) == (2 if nested else 3)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
