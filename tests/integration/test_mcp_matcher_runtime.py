"""Typed managed identity rules govern actual discovery, execution and refresh."""

import asyncio
from dataclasses import replace

import pytest
from test_mcp_server_requirements import make_runtime
from test_mcp_tool_approval import Model

from corki.config import MCPServerSettings, mcp_regex
from corki.protocol.events import TurnCompleted
from corki.protocol.items import ToolCallItem, ToolResultItem


@pytest.mark.parametrize("mode", ["direct", "native", "compatible", "code_mode"])
@pytest.mark.parametrize("failure", [None, "identity", "engine"])
@pytest.mark.parametrize("transport", ["stdio", "http"])
def test_typed_identity_controls_discovery_and_actual_effect(
    tmp_path, monkeypatch, mode, failure, transport
):
    async def scenario():
        if transport == "stdio":
            original = MCPServerSettings(
                "docs", "stdio", command="company-cli", args=("mcp", "trusted")
            )
            changed = replace(original, args=("mcp", "untrusted"))
            identity = {
                "command": {
                    "executable": "company-cli",
                    "args": [
                        {"match": "exact", "value": "mcp"},
                        {"match": "regex", "expression": "trusted"},
                    ],
                }
            }
        else:
            original = MCPServerSettings("docs", "http", url="https://trusted.test/mcp")
            changed = replace(original, url="https://untrusted.test/mcp")
            identity = {"url": {"match": "regex", "expression": r"https://trusted\.test/mcp"}}

        class ChangingModel(Model):
            async def stream(self, request):
                async for event in super().stream(request):
                    if failure and any(
                        isinstance(i, ToolCallItem) and i.call.name != "tool_search"
                        for i in event.items
                    ):
                        if failure == "engine":
                            monkeypatch.setattr(mcp_regex, "_FUEL", 0)
                        runtime.request_mcp_reconcile(
                            (changed if failure == "identity" else original,)
                        )
                    yield event

        runtime, clients, model = await make_runtime(
            tmp_path,
            monkeypatch,
            mode=mode,
            model=ChangingModel(mode),
            servers=(original,),
            requirements={"mcp_servers": {"docs": {"identity": identity}}},
        )
        try:
            events = [event async for event in runtime.stream("needle")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(clients) == 1
            assert clients[0].calls == ([] if failure else [("write", {"value": 1})])
            results = [
                item for item in model.requests[-1].items if isinstance(item, ToolResultItem)
            ]
            assert ("disabled by managed requirements" if failure else "remote effect") in repr(
                results
            )
        finally:
            await runtime.aclose()
        assert clients[0].closed

    asyncio.run(scenario())


@pytest.mark.parametrize("expression", ["[", "(?x)mcp # trailing comment"])
@pytest.mark.parametrize("plugin", [False, True])
def test_invalid_regex_fails_before_runtime_resources(tmp_path, monkeypatch, expression, plugin):
    def forbidden(*args, **kwargs):
        pytest.fail("allocated resources before regex validation")

    for owner in ("ProcessManager", "SQLiteSessionRepository", "PluginManager.discover_and_load"):
        monkeypatch.setattr("corki.core.runtime." + owner, forbidden)
    rules = {
        "mcp_servers": {
            "docs": {
                "identity": {
                    "url": {
                        "match": "regex",
                        "expression": expression,
                    }
                }
            }
        }
    }
    requirements = {"plugins": {"package": rules}} if plugin else rules
    with pytest.raises(ValueError):
        asyncio.run(make_runtime(tmp_path, monkeypatch, requirements=requirements))
    assert not (tmp_path / "history.db").exists()


@pytest.mark.parametrize(
    "identity,server",
    [
        (
            {"url": {"match": "prefix", "value": "https://trusted.test/"}},
            MCPServerSettings(
                "docs",
                "http",
                url="https://trusted.test.evil/mcp",
                http_headers_helper="must-not-run",
            ),
        ),
        (
            {"url": {"match": "regex", "expression": "trusted"}},
            MCPServerSettings(
                "docs", "http", url="https://untrusted.test", http_headers_helper="must-not-run"
            ),
        ),
        (
            {"command": {"executable": "server", "args": []}},
            MCPServerSettings("docs", "stdio", command="server", args=("extra",)),
        ),
        (
            {
                "command": {
                    "executable": "server",
                    "args": [{"match": "prefix", "value": "trusted-"}],
                }
            },
            MCPServerSettings("docs", "stdio", command="server", args=("untrusted",)),
        ),
    ],
)
def test_typed_denial_happens_before_any_client_or_helper(tmp_path, monkeypatch, identity, server):
    async def scenario():
        runtime, clients, model = await make_runtime(
            tmp_path,
            monkeypatch,
            servers=(server,),
            requirements={"mcp_servers": {"docs": {"identity": identity}}},
        )
        try:
            events = [e async for e in runtime.stream("hello")]
            assert isinstance(events[-1], TurnCompleted)
            assert clients == []
            assert not any(t.name.startswith("mcp__docs") for t in model.requests[0].tools)
            assert "managed requirements" in repr(runtime._mcp_manager.warnings)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
