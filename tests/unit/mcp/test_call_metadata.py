"""Ordinary MCP metadata is detached and never enables Apps account semantics."""

import asyncio
from copy import deepcopy
from dataclasses import replace

import pytest
from test_mcp import FakeMCPClient

from corki.config import MCPServerSettings
from corki.mcp.call_metadata import call_metadata
from corki.mcp.manager import MCPManager
from corki.mcp.projection import project_tool, raw_call_bindings
from corki.tools import ToolRegistry


@pytest.mark.parametrize("server", ["codex_apps", "ordinary"])
@pytest.mark.parametrize(
    "argument",
    [
        None,
        {},
        {"link_id": None},
        {"link_id": 42},
        {"link_id": "\u2003 "},
        {"link_id": " selected "},
        {"link_id": "\u001c"},
    ],
)
def test_remote_account_metadata_never_requires_or_consumes_business_selector(server, argument):
    tool = project_tool(
        server,
        {
            "name": "read",
            "_meta": {
                "link_id": "default",
                "connector_id": "mail",
                "connector_name": "Mail",
                "_codex_apps": {
                    "requires_explicit_link_id": True,
                    "connected_account_email": " mail@test ",
                },
            },
        },
    )
    before = deepcopy(argument)
    metadata = call_metadata(tool, argument)
    assert metadata.link_id is None
    assert metadata.connected_account_email is None
    assert metadata.connector_id is None
    assert metadata.codex_apps_meta is None
    assert argument == before


@pytest.mark.parametrize("flag", [False, None, "true", 1, [], {}])
def test_legacy_selector_flags_do_not_enable_account_selection(flag):
    projection = project_tool(
        "codex_apps",
        {
            "name": "read",
            "_meta": {"link_id": " default ", "_codex_apps": {"requires_explicit_link_id": flag}},
        },
    )
    assert call_metadata(projection, {"link_id": "other"}).link_id is None


@pytest.mark.parametrize("value", [None, 42, "", "\u2003"])
def test_invalid_optional_catalog_link_does_not_block_legacy(value):
    projection = project_tool("codex_apps", {"name": "read", "_meta": {"link_id": value}})
    assert call_metadata(projection, {}).link_id is None


def test_raw_binding_uses_ordinary_first_identity_without_connector_sorting():
    definitions = [
        {
            "name": "Read",
            "_meta": {"connector_id": id, "connector_name": name},
            "description": description,
        }
        for id, name, description in [
            ("z", "A", "earlier"),
            ("a", "Z", "winner"),
            ("a", "Z", "duplicate ignored"),
        ]
    ]
    projections = [project_tool("codex_apps", d) for d in definitions]
    assert raw_call_bindings(projections)["Read"].definition["description"] == "earlier"
    assert (
        raw_call_bindings([projections[1], projections[0], projections[2]])["Read"].definition[
            "description"
        ]
        == "winner"
    )
    ordinary = [project_tool("ordinary", d) for d in definitions]
    assert raw_call_bindings(ordinary)["Read"].definition["description"] == "earlier"


@pytest.mark.parametrize("refresh", [False, True])
def test_prepared_metadata_is_detached_and_tracks_exact_connection_generation(
    tmp_path, monkeypatch, refresh
):
    async def scenario():
        definitions = [
            {
                "name": "read",
                "description": "old description",
                "inputSchema": {"type": "object"},
                "annotations": {"readOnlyHint": True},
                "_meta": {
                    "connector_id": "old",
                    "connector_name": "Old",
                    "_codex_apps": {"requires_explicit_link_id": True},
                },
            }
        ]
        calls, clients = [], []

        class Client(FakeMCPClient):
            async def list_tools(self):
                return deepcopy(tuple(definitions))

            async def call_tool(self, name, arguments):
                calls.append((self.settings.url, arguments))
                return {"content": []}

        def factory(settings):
            client = Client(settings)
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        settings = MCPServerSettings("codex_apps", "http", url="https://old.invalid")
        manager = MCPManager((settings,), ToolRegistry())
        try:
            await manager.start()
            async with manager.prepare_call("codex_apps", "read") as prepared:
                exported = prepared.tool_info
                exported.definition["annotations"]["readOnlyHint"] = False
                exported.definition["description"] = "tampered"
                assert prepared.metadata_for({}).annotations.read_only is True
                assert prepared.metadata_for({}).tool_description == "old description"
                await prepared.call({})
                assert calls == [(settings.url, {})]
                assert prepared.metadata_for({"link_id": " selected "}).link_id is None
                definitions[0]["_meta"] = {"connector_id": "new", "connector_name": "New"}
                definitions[0]["description"] = "new description"
                if refresh:
                    manager.request_refresh()
                    await manager.refresh_if_dirty()
                else:
                    manager.request_reconcile((replace(settings, url="https://new.invalid"),))
                    await manager.start()
                assert prepared.metadata_for({}).tool_description == "old description"
                async with manager.prepare_call("codex_apps", "read") as latest:
                    assert latest.metadata_for({}).tool_description == "new description"
                    assert latest.metadata_for({}).connector_id is None
                    await latest.call({})
                await prepared.call({"link_id": "selected"})
                assert calls[-1] == ("https://old.invalid", {"link_id": "selected"})
                assert len(clients) == 2
        finally:
            await manager.aclose()
        assert all(c.closed for c in clients)

    asyncio.run(scenario())
