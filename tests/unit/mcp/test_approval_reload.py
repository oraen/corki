"""Approval reloads derive policy from declarations, not a previously restricted view."""

import asyncio
import tomllib
from dataclasses import replace

import pytest

from corki.config import MCPServerSettings
from corki.config.features import parse_mcp_servers
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.mcp.approval_persistence import MCPApprovalPersistence
from corki.mcp.catalog import MCPCatalog, MCPCatalogSource, MCPRegistration
from corki.mcp.client import MCPClient, MCPProtocolError
from corki.mcp.manager import MCPManager
from corki.tools import ToolRegistry


def definition(name):
    return {"name": name, "inputSchema": {"type": "object"}}


class Client(MCPClient):
    def __init__(self, settings):
        super().__init__(settings)
        self.tools = (definition("old"),)
        self.calls = []
        self.closed = False

    async def start(self):
        pass

    async def list_tools(self):
        return self.tools

    async def call_tool(self, name, arguments):
        self.calls.append(name)
        return {"content": [{"type": "text", "text": name}]}

    async def aclose(self):
        self.closed = True

    async def _exchange(self, message):
        raise AssertionError(message)

    async def _send_notification(self, message):
        raise AssertionError(message)


TRIGGER = '[mcp.servers.codex_apps]\ntransport="http"\nurl="https://fixture.invalid"\n'


@pytest.mark.parametrize("kind", ["plugin", "selected_plugin"])
def test_reload_removes_plugin_filters_and_caps_from_original_declarations(
    tmp_path, monkeypatch, kind
):
    async def scenario():
        config = tmp_path / "config.toml"
        original = TRIGGER + (
            '[plugins.fixture.mcp_servers.docs]\nenabled_tools=["old"]\ndisabled_tools=["new"]\n'
            "[plugins.fixture.mcp_servers.docs.tools.old]\noutput_token_limit=5\n"
        )
        config.write_text(original)
        persistence = MCPApprovalPersistence(
            LocalConfigState((ConfigLayer(config, "user", contents=original),)), tmp_path
        )
        docs = MCPServerSettings(
            "docs", "http", url="https://docs.invalid", tool_output_token_limits=(("old", 100),)
        )
        apps = replace(
            docs, name="codex_apps", url="https://apps.invalid", tool_output_token_limits=()
        )
        clients, limits = [], []

        def factory(settings):
            client = Client(settings)
            if settings.name == "docs":
                client.tools = tuple(
                    {**definition(name), "annotations": {"readOnlyHint": True}}
                    for name in ("old", "new")
                )
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        registry = ToolRegistry()
        manager = MCPManager(
            (),
            registry,
            catalog=MCPCatalog(
                (MCPRegistration(docs, MCPCatalogSource(kind, "fixture")), MCPRegistration(apps))
            ),
            approval_policy="on-request",
            approval_persistence=persistence,
        )

        async def host(request):
            assert request.server_name == "codex_apps"
            config.write_text(TRIGGER + "# remove plugin policy\n")
            manager.elicitations.respond(
                request.server_name, request.request_id, "accept", meta={"persist": "always"}
            )

        manager.elicitations.handler = host
        try:
            await manager.start()
            assert registry.get("mcp__docs::new") is None
            async with manager.prepare_call("docs", "old") as captured:
                await captured.call({}, on_output_token_limit=limits.append)
                await manager.call_tool("codex_apps", "old", {})
                await manager.refresh_if_dirty()
                assert captured.connection.settings.tool_output_token_limits == (("old", 5),)
                assert registry.get("mcp__docs::new") is not None
                await manager.call_tool("docs", "old", {}, on_output_token_limit=limits.append)
                await manager.call_tool("docs", "new", {})
            assert limits == [5, 100]
            assert sum(client.calls.count("new") for client in clients) == 1
        finally:
            await manager.aclose()
        assert all(client.closed for client in clients)

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["plugin", "selected_plugin"])
@pytest.mark.parametrize("scope", ["default", "tool"])
@pytest.mark.parametrize("next_policy", ["removed", "approve"])
@pytest.mark.parametrize("baseline", ["initial", "catalog", "reconcile"])
def test_persistent_reload_recomputes_plugin_policy_from_declaration(
    tmp_path, monkeypatch, kind, scope, next_policy, baseline
):
    async def scenario():
        config = tmp_path / "config.toml"
        prefix = "[plugins.fixture.mcp_servers.docs"
        section, field = (
            (prefix + "]", "default_tools_approval_mode")
            if scope == "default"
            else (prefix + ".tools.old]", "approval_mode")
        )
        original = TRIGGER + f'{section}\n{field}="prompt"\n'
        config.write_text(original)
        persistence = MCPApprovalPersistence(
            LocalConfigState((ConfigLayer(config, "user", contents=original),)), tmp_path
        )
        docs = MCPServerSettings("docs", "http", url="https://docs.invalid")
        apps = replace(docs, name="codex_apps", url="https://apps.invalid")
        clients, reviews = {}, []

        def factory(settings):
            client = Client(settings)
            if settings.name == "docs":
                client.tools = ({**definition("old"), "annotations": {"readOnlyHint": True}},)
            clients[settings.name] = client
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        manager = MCPManager(
            (),
            ToolRegistry(),
            catalog=MCPCatalog(
                (
                    MCPRegistration(docs, MCPCatalogSource(kind, "fixture")),
                    MCPRegistration(apps, MCPCatalogSource("compatibility", "host")),
                )
            ),
            approval_policy="on-request",
            approval_persistence=persistence,
        )

        async def host(request):
            reviews.append(request.server_name)
            manager.elicitations.respond(
                request.server_name,
                request.request_id,
                "accept" if request.server_name == "codex_apps" else "decline",
                meta={"persist": "always"},
            )

        manager.elicitations.handler = host
        try:
            await manager.start()
            async with manager.prepare_call("docs", "old") as captured:
                with pytest.raises(MCPProtocolError, match="rejected"):
                    await captured.call({})
                if baseline != "initial":
                    docs = replace(docs, default_tools_approval_mode="writes", timeout_seconds=80)
                    if baseline == "catalog":
                        manager.request_catalog(
                            MCPCatalog(
                                (
                                    MCPRegistration(docs, MCPCatalogSource(kind, "fixture")),
                                    MCPRegistration(
                                        apps, MCPCatalogSource("compatibility", "host")
                                    ),
                                )
                            )
                        )
                    else:
                        manager.request_reconcile((docs, apps))
                config.write_text(
                    TRIGGER
                    + (
                        "# policy removed\n"
                        if next_policy == "removed"
                        else f'{section}\n{field}="approve"\n'
                    )
                )
                await manager.call_tool("codex_apps", "old", {})
                await manager.refresh_if_dirty()
                # Already admitted calls retain the old prompt policy.
                with pytest.raises(MCPProtocolError, match="rejected"):
                    await captured.call({})
            expected_default = None if baseline == "initial" else "writes"
            expected_tools = ()
            if next_policy == "approve":
                granted = "approve" if kind == "plugin" else expected_default or "auto"
                if scope == "default":
                    expected_default = granted
                else:
                    expected_tools = (("old", granted),)
            async with manager.prepare_call("docs", "old") as updated:
                assert updated.connection.settings == replace(
                    docs,
                    default_tools_approval_mode=expected_default,
                    tool_approval_modes=expected_tools,
                )
            result = await manager.call_tool("docs", "old", {})
            assert result == {"content": [{"type": "text", "text": "old"}]}
            assert reviews == ["docs", "codex_apps", "docs"]
            assert clients["docs"].calls == ["old"]
            assert clients["codex_apps"].calls == ["old"]
        finally:
            await manager.aclose()
        assert all(client.closed for client in clients.values())

    asyncio.run(scenario())


@pytest.mark.parametrize("catalog_backed", [False, True])
def test_ordinary_file_reload_keeps_materialized_server_policy_until_host_refresh(
    tmp_path, monkeypatch, catalog_backed
):
    async def scenario():
        config = tmp_path / "config.toml"
        original = (
            '[mcp.servers.docs]\ntransport="http"\nurl="https://fixture.invalid"\n'
            '[mcp.servers.docs.tools.read]\napproval_mode="prompt"\n'
        )
        config.write_text(original)
        settings = parse_mcp_servers(tomllib.loads(original)["mcp"]["servers"])
        state = LocalConfigState((ConfigLayer(config, "user", contents=original),))
        persistence = MCPApprovalPersistence(state, tmp_path)
        clients = []

        def factory(settings):
            client = Client(settings)
            client.tools = (
                definition("old"),
                {**definition("read"), "annotations": {"readOnlyHint": True}},
            )
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        manager = MCPManager(
            () if catalog_backed else settings,
            ToolRegistry(),
            catalog=MCPCatalog(tuple(MCPRegistration(s) for s in settings))
            if catalog_backed
            else None,
            approval_policy="on-request",
            approval_persistence=persistence,
        )
        reviews = []

        async def host(request):
            reviews.append(request)
            if len(reviews) == 1:
                config.write_text(original.replace("prompt", "approve"))
            manager.elicitations.respond(
                request.server_name,
                request.request_id,
                "accept" if len(reviews) == 1 else "decline",
                meta={"persist": "always"},
            )

        manager.elicitations.handler = host
        try:
            await manager.call_tool("docs", "old", {})
            with pytest.raises(MCPProtocolError, match="rejected"):
                await manager.call_tool("docs", "read", {})
            await manager.call_tool("docs", "old", {})
            assert clients[0].calls == ["old", "old"] and len(reviews) == 2
            updated = parse_mcp_servers(tomllib.loads(config.read_text())["mcp"]["servers"])
            assert dict(updated[0].tool_approval_modes) == {"read": "approve", "old": "approve"}
            # Explicit materialized host refresh, unlike legacy file reload, applies it.
            manager.request_reconcile(updated)
            await manager.call_tool("docs", "read", {})
            assert clients[0].calls == ["old", "old", "read"] and len(reviews) == 2
        finally:
            await manager.aclose()
        assert all(client.closed for client in clients)

    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["user", "project", "bad_toml", "bad_shell"])
def test_write_reloads_user_layers_without_replacing_other_authority(tmp_path, monkeypatch, case):
    async def scenario():
        user = tmp_path / "base.toml"
        profile = tmp_path / "profile.toml"
        project = tmp_path / "project.toml"
        policy = '[plugins.fixture.mcp_servers.codex_apps.tools.read]\napproval_mode="prompt"\n'
        user.write_text(policy)
        profile.write_text(TRIGGER + "# selected user profile\n")
        project.write_text(policy if case == "project" else "")
        state = LocalConfigState(
            (
                ConfigLayer(user, "user", contents=user.read_text()),
                ConfigLayer(profile, "user", contents=profile.read_text()),
                ConfigLayer(project, "project", contents=project.read_text()),
            )
        )
        persistence = MCPApprovalPersistence(state, tmp_path)
        settings = MCPServerSettings(
            "codex_apps",
            "http",
            url="https://fixture.invalid",
        )
        client = Client(settings)
        client.tools = (
            definition("old"),
            {**definition("read"), "annotations": {"readOnlyHint": True}},
        )
        monkeypatch.setattr("corki.mcp.manager.create_client", lambda _: client)
        manager = MCPManager(
            (settings,),
            ToolRegistry(),
            approval_policy="on-request",
            approval_persistence=persistence,
            catalog=MCPCatalog(
                (
                    MCPRegistration(
                        settings,
                        MCPCatalogSource("plugin", "fixture"),
                    ),
                )
            ),
        )
        reviews = []
        updated_user = (
            "invalid=["
            if case == "bad_toml"
            else '[shell_environment_policy]\nfilters="invalid"\n'
            if case == "bad_shell"
            else policy.replace("prompt", "approve")
        )

        async def host(request):
            reviews.append(request)
            if len(reviews) == 1:
                user.write_text(updated_user)
                project.write_text(policy.replace("prompt", "approve"))
            manager.elicitations.respond(
                request.server_name,
                request.request_id,
                "accept" if len(reviews) == 1 else "decline",
                meta={"persist": "always"},
            )

        manager.elicitations.handler = host
        try:
            await manager.start()
            async with manager.prepare_call("codex_apps", "read") as captured:
                await manager.call_tool("codex_apps", "old", {})
                await manager.refresh_if_dirty()
                with pytest.raises(MCPProtocolError, match="rejected"):
                    await captured.call({})
            if case == "user":
                await manager.call_tool("codex_apps", "read", {})
            else:
                with pytest.raises(MCPProtocolError, match="rejected"):
                    await manager.call_tool("codex_apps", "read", {})
            # Reload failure must still retain the accepted session consent.
            await manager.call_tool("codex_apps", "old", {})
            assert client.calls == (["old", "read", "old"] if case == "user" else ["old", "old"])
            assert len(reviews) == (2 if case == "user" else 3)
            assert user.read_text() == updated_user
            assert tomllib.loads(profile.read_text())["mcp"]["servers"]["codex_apps"]["tools"][
                "old"
            ] == {"approval_mode": "approve"}
            assert "# selected user profile" in profile.read_text()
            assert persistence.configuration.layers[-1] == state.layers[-1]
            if case in {"bad_toml", "bad_shell"}:
                assert persistence.configuration is state
            else:
                assert persistence.configuration.layers[0].contents == updated_user
        finally:
            await manager.aclose()
        assert client.closed

    asyncio.run(scenario())
