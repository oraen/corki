"""Legacy user reload must update live plugin/skill contributions, not just MCP consent."""

import asyncio
import json
import threading
import tomllib
from dataclasses import replace

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.context.world_state import snapshot_content
from corki.core import LangGraphRuntime
from corki.mcp.catalog import MCPCatalog, MCPCatalogSource, MCPRegistration
from corki.mcp.client import HttpMCPClient
from corki.models import ModelCompleted
from corki.plugins.manager import discover_manifests
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCancelled, TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    ToolCallItem,
    ToolResultItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("trigger_name", ["approval_trigger", "codex_apps"])
@pytest.mark.parametrize("mode", ["compatible", "native", "code_mode"])
@pytest.mark.parametrize(
    "change",
    [
        "plugin",
        "skill",
        "unchanged",
        "retired_apps",
        "bad_write",
        "reenable",
        "manifest",
        "code",
        "bad_manifest",
        "remove",
        "host_override",
        "feature_static",
        "feature_static_off",
        "code_failure",
        "host_catalog",
        "host_reconcile",
        "slow_close",
        "slow_host_catalog",
        "implicit_project",
        "project_directory",
        "user_directory_add",
        "host_empty_dirs",
        "mcp_disabled",
        "mcp_allow",
        "mcp_deny",
        "mcp_limit",
        "mcp_enable",
        "package_disabled",
        "package_reenable",
        "package_removed",
        "package_host_override",
        "package_project",
    ],
)
def test_approval_reload_updates_next_turn_plugin_and_skill_views(
    tmp_path, monkeypatch, mode, change, trigger_name
):
    async def scenario():
        home = tmp_path / "home"
        package = home / "plugins/reload_fixture"
        manifest = package / ".codex-plugin/plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps(
                {
                    "name": "reload_fixture",
                    "description": "Reload plugin fixture",
                    "entrypoint": "plugin.py:register",
                    "mcpServers": {"docs": {"type": "http", "url": "https://docs.invalid"}},
                }
            )
        )
        skill = package / "skills/guide/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(
            "---\nname: guide\ndescription: RELOAD_SKILL_MARKER\n---\nFixture guide.\n"
        )
        plugin_code = package / "plugin.py"
        source = (
            "from pathlib import Path\n"
            "def register(api):\n"
            "    with Path(__file__).with_name('registrations').open('a') as f: "
            "f.write('register\\n')\n"
            "    api.register_tool(name='echo', description='plugin echo fixture', "
            "parameters={'type':'object'}, handler=lambda a,c:'echo version one')\n"
            "async def aclose():\n"
            "    with Path(__file__).with_name('closures').open('a') as f: f.write('close\\n')\n"
        )
        plugin_code.write_text(source)
        config = tmp_path / "config.toml"
        original = (
            '[mcp]\napproval_policy="on-request"\n'
            f'[mcp.servers.{trigger_name}]\ntransport="http"\nurl="https://approval.invalid"\n'
        )
        extra_root = tmp_path / ".corki/plugins"
        if change == "project_directory":
            extra_root = tmp_path / "project-extra"
            original += f'[projects.{json.dumps(str(tmp_path))}]\ntrust_level="trusted"\n'
            (tmp_path / ".corki").mkdir()
            (tmp_path / ".corki/config.toml").write_text(
                '[plugins]\ndirectories=["../project-extra"]\n'
            )
        if change == "package_project":
            original += f'[projects.{json.dumps(str(tmp_path))}]\ntrust_level="trusted"\n'
            (tmp_path / ".corki").mkdir()
            (tmp_path / ".corki/config.toml").write_text(
                "[plugins.reload_fixture]\nenabled=false\n"
            )
        config.write_text(
            original
            + ('[plugins]\ndisabled=["reload_fixture"]\n' if change == "reenable" else "")
            + (
                "[plugins.reload_fixture]\nenabled=false\n"
                if change in {"package_reenable", "package_removed"}
                else ""
            )
            + ("[features]\nplugins=false\n" if change == "feature_static_off" else "")
            + (
                "[plugins.reload_fixture.mcp_servers.docs]\nenabled=false\n"
                if change == "mcp_enable"
                else ""
            )
        )
        clients, calls, reviews, requests, observations = [], [], [], [], []
        nested = mode == "code_mode"
        echo_name = "plugin__reload_fixture__echo"

        def factory(settings):
            async def respond(request):
                packet = json.loads(request.content)
                if packet["method"] == "notifications/initialized":
                    return httpx.Response(202)
                if packet["method"] == "initialize":
                    result = {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": {"name": "fixture", "version": "1"},
                    }
                elif packet["method"] == "tools/list":
                    trigger = settings.name == trigger_name
                    result = {
                        "tools": [
                            {
                                "name": "write" if trigger else "read",
                                "description": "approval reload trigger"
                                if trigger
                                else "plugin reload document",
                                "inputSchema": {"type": "object"},
                                "annotations": {"readOnlyHint": not trigger},
                            }
                        ]
                    }
                else:
                    assert packet["method"] == "tools/call"
                    calls.append((settings.name, packet["params"]["name"]))
                    result = {
                        "content": [
                            {
                                "type": "text",
                                "text": "fixture result" * 200
                                if change == "mcp_limit" and settings.name == "docs"
                                else "fixture result",
                            }
                        ]
                    }
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": packet["id"], "result": result}
                )

            client = HttpMCPClient(settings, transport=httpx.MockTransport(respond))
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)

        class Model:
            phase = 1
            steps = 0

            async def stream(self, request):
                self.steps += 1
                requests.append((self.phase, request))
                turn, step = request.items[-1].turn_id, new_step_id()
                description = (
                    "approval reload trigger" if self.phase == 1 else "plugin reload document"
                )
                call = None
                if nested and self.steps == 1:
                    code = (
                        "const s=ALL_TOOLS.find(t=>t.name.endsWith('skill_list'));"
                        "text(await tools[s.name]({}));"
                        if self.phase == 2
                        else ""
                    )
                    code += (
                        "const t=ALL_TOOLS.find(t=>t.description.includes("
                        + json.dumps(description)
                        + "));if(t)text(await tools[t.name]({}));"
                    )
                    code += (
                        "const p=ALL_TOOLS.find(t=>t.description.includes('plugin echo fixture'));"
                        "if(p)text(await tools[p.name]({}));"
                    )
                    call = ToolCall(
                        new_tool_call_id(), "exec", None, input_kind="freeform", raw_arguments=code
                    )
                elif not nested:
                    search_step = 1 if self.phase == 1 else 2
                    if self.phase == 2 and self.steps == 1:
                        call = ToolCall(new_tool_call_id(), "skill_list", {})
                    elif self.steps == search_step:
                        call = ToolCall(new_tool_call_id(), "tool_search", {"query": description})
                    elif self.steps == search_step + 1:
                        results = [i for i in request.items if isinstance(i, ToolResultItem)]
                        found = next(
                            (
                                t
                                for t in results[-1].discovered_tools
                                if description in t.description
                            ),
                            None,
                        )
                        if found is not None:
                            call = ToolCall(new_tool_call_id(), found.name, {})
                    elif (
                        self.phase == 2
                        and self.steps == search_step + 2
                        and any(t.name == echo_name for t in request.tools)
                    ):
                        call = ToolCall(new_tool_call_id(), echo_name, {})
                if call is not None:
                    items = (ToolCallItem(call, turn, step),)
                    if (
                        not nested
                        and self.phase == 1
                        and self.steps == 2
                        and change
                        not in {
                            "reenable",
                            "feature_static_off",
                            "package_reenable",
                            "package_removed",
                            "package_project",
                        }
                    ):
                        # Captured together with approval: disabling/replacing the
                        # plugin must not retarget this already-admitted handler.
                        items += (
                            ToolCallItem(ToolCall(new_tool_call_id(), echo_name, {}), turn, step),
                        )
                    yield ModelCompleted(items)
                else:
                    observations.extend(
                        i
                        for i in request.items
                        if isinstance(i, ToolResultItem) and i.turn_id == turn
                    )
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        model = Model()
        settings = replace(
            CorkiSettings.for_directory(tmp_path, config_file=config),
            execution_permissions=None,
            api_mode="responses",
            model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
            tool_search_mode="disabled" if nested else mode,
            tool_mode="code_mode_only" if nested else "direct",
        )
        if change in {"host_override", "package_host_override"}:
            settings = replace(settings, disabled_plugins=frozenset())
        if change == "host_empty_dirs":
            settings = replace(settings, plugin_dirs=())
        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            model=model,
            registry=ToolRegistry(),
            database_path=tmp_path / "runtime.db",
            home_path=home,
        )

        async def host(request):
            reviews.append(request)
            extra = (
                '[plugins]\ndisabled=["reload_fixture"]\n'
                if change
                in {
                    "plugin",
                    "host_override",
                    "host_catalog",
                    "host_reconcile",
                    "slow_close",
                    "slow_host_catalog",
                }
                else '[[skills.config]]\nname="reload_fixture:guide"\nenabled=false\n'
                if change == "skill"
                else ""
            )
            if change == "feature_static":
                extra = "[features]\nplugins=false\n"
            if change == "retired_apps":
                extra = (
                    '[apps.fixture]\nenabled="ignored legacy platform setting"\n'
                    '[[skills.config]]\nname="reload_fixture:guide"\nenabled=false\n'
                )
            if change == "feature_static_off":
                extra = "[features]\nplugins=true\n"
            if change in {"package_disabled", "package_host_override"}:
                extra = "[plugins.reload_fixture]\nenabled=false\n"
            if change in {"package_reenable", "package_project"}:
                extra = "[plugins.reload_fixture]\nenabled=true\n"
            if change in {"mcp_disabled", "mcp_allow", "mcp_deny", "mcp_limit"}:
                extra = (
                    "[plugins.reload_fixture.mcp_servers.docs]\n"
                    + {
                        "mcp_disabled": "enabled=false\n",
                        "mcp_allow": "enabled_tools=[]\n",
                        "mcp_deny": 'disabled_tools=["read"]\n',
                        "mcp_limit": (
                            "[plugins.reload_fixture.mcp_servers.docs.tools.read]\n"
                            "output_token_limit=20\n"
                        ),
                    }[change]
                )
            if change in {
                "implicit_project",
                "project_directory",
                "user_directory_add",
                "host_empty_dirs",
            }:
                candidate = extra_root / "new_fixture"
                candidate_manifest = candidate / ".codex-plugin/plugin.json"
                candidate_manifest.parent.mkdir(parents=True)
                candidate_manifest.write_text(
                    json.dumps({"name": "new_fixture", "entrypoint": "plugin.py:register"})
                )
                (candidate / "plugin.py").write_text(
                    "from pathlib import Path\n"
                    "def register(api):\n"
                    "    Path(__file__).with_name('registered').write_text('registered')\n"
                )
                if change in {"user_directory_add", "host_empty_dirs"}:
                    extra = f"[plugins]\ndirectories=[{json.dumps(str(extra_root))}]\n"
            if change == "manifest":
                value = json.loads(manifest.read_text())
                value["description"] = "new manifest description"
                value["mcpServers"]["docs"]["url"] = "https://docs-new.invalid"
                manifest.write_text(json.dumps(value))
            if change in {"code", "code_failure"}:
                plugin_code.write_text(source.replace("echo version one", "echo version two"))
            if change == "code_failure":

                def fail(*args, **kwargs):
                    raise ValueError("reject newly registered plugin candidate")

                monkeypatch.setattr(runtime, "_prepare_mcp_configuration", fail)
            if change == "bad_manifest":
                manifest.write_text("{invalid")
            if change == "remove":
                manifest.unlink()
            if change in {"host_catalog", "host_reconcile"}:
                host_docs = MCPServerSettings("docs", "http", url="https://host-docs.invalid")
                if change == "host_catalog":
                    runtime.request_mcp_catalog(
                        MCPCatalog(
                            (
                                *(MCPRegistration(server) for server in settings.mcp_servers),
                                MCPRegistration(host_docs, MCPCatalogSource("extension", "host")),
                            )
                        )
                    )
                else:
                    runtime.request_mcp_reconcile((*settings.mcp_servers, host_docs))
            config.write_text("invalid=[" if change == "bad_write" else original + extra)
            runtime.respond_mcp_elicitation(
                request.server_name, request.request_id, "accept", meta={"persist": "always"}
            )

        runtime.set_mcp_elicitation_handler(host)
        entered = asyncio.Event()
        release = threading.Event()
        owned = []
        if change in {"slow_close", "slow_host_catalog"}:
            loop = asyncio.get_running_loop()

            def scan(*args):
                candidate = discover_manifests(*args)
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(10), "test must release owned plugin scan"
                return candidate

            monkeypatch.setattr("corki.core.runtime.discover_manifests", scan)

        async def collect():
            events = []
            try:
                async for event in runtime.stream("run the approval reload trigger"):
                    events.append(event)
            except asyncio.CancelledError:
                if change != "slow_close":
                    raise
                assert isinstance(events[-1], TurnCancelled)
            return events

        try:
            if change in {"slow_close", "slow_host_catalog"}:
                worker = asyncio.create_task(collect())
                owned.append(worker)
                await asyncio.wait_for(entered.wait(), 5)
                if change == "slow_close":
                    closing = asyncio.create_task(runtime.aclose())
                    owned.append(closing)
                    await asyncio.sleep(0.01)
                    assert not closing.done() and not worker.done()
                else:
                    runtime.request_mcp_catalog(
                        MCPCatalog(
                            (
                                *(MCPRegistration(server) for server in settings.mcp_servers),
                                MCPRegistration(
                                    MCPServerSettings(
                                        "docs", "http", url="https://host-docs.invalid"
                                    ),
                                    MCPCatalogSource("extension", "late-host"),
                                ),
                            )
                        )
                    )
                release.set()
                first = await asyncio.wait_for(worker, 5)
                if change == "slow_close":
                    await asyncio.wait_for(closing, 5)
                    assert sum(isinstance(event, TurnCancelled) for event in first) == 1
                    assert not calls
                    assert (
                        runtime._mcp_manager._approval_document["mcp"]["servers"][trigger_name].get(
                            "tools"
                        )
                        is None
                    )
                    assert runtime._pending_plugins is None
                    assert all(client.is_closed for client in clients)
                    assert (package / "registrations").read_text().splitlines() == ["register"]
                    assert (package / "closures").read_text().splitlines() == ["close"]
                    return
            else:
                first = await collect()
            if change == "code_failure":
                assert (package / "closures").read_text().splitlines() == ["close"]
                assert (
                    runtime._mcp_manager._approval_document["mcp"]["servers"][trigger_name].get(
                        "tools"
                    )
                    is None
                )
            model.phase, model.steps = 2, 0
            second = [
                event
                async for event in runtime.stream("list skills and search the plugin document")
            ]
            catalog = await runtime.mcp_tool_catalog()
            plugin_callable = runtime._registry.get(echo_name) is not None
            if change == "manifest":
                assert (
                    runtime._plugin_manager.plugins[0].manifest.description
                    == "new manifest description"
                )
                assert any(
                    entry.settings.url == "https://docs-new.invalid"
                    for entry in runtime.mcp_catalog.servers
                )
            if change in {"host_catalog", "host_reconcile", "slow_host_catalog"}:
                assert any(
                    entry.settings.url == "https://host-docs.invalid"
                    for entry in runtime.mcp_catalog.servers
                )
        finally:
            release.set()
            await asyncio.gather(*owned, return_exceptions=True)
            await runtime.aclose()
        assert all(client.is_closed for client in clients)
        assert isinstance(first[-1], TurnCompleted) and isinstance(second[-1], TurnCompleted), (
            first[-1],
            second[-1],
        )
        assert len(reviews) == 1
        retained_guidance = None
        for _, request in requests:
            contexts = [item for item in request.items if isinstance(item, ContextItem)]
            guidance = [
                item for item in contexts if item.content_kind == "plugins.usage_instructions"
            ]
            assert len(guidance) <= 1
            if retained_guidance is not None:
                assert guidance == [retained_guidance]
            if guidance:
                retained_guidance = guidance[0]
                assert "Plugins are not invoked directly" in retained_guidance.content
                assert "Reload plugin fixture" not in retained_guidance.content
            current_catalog = next(
                (i for i in reversed(contexts) if i.key == "extensions.plugins.catalog"), None
            )
            if current_catalog is not None and any(
                name in snapshot_content(current_catalog)
                for name in ("- reload_fixture", "- new_fixture")
            ):
                assert guidance, "an available local plugin must introduce generic guidance"
            assert not any(
                "extensions.plugins.guidance" in item.content and "no longer apply" in item.content
                for item in contexts
            )
        if change in {
            "implicit_project",
            "project_directory",
            "user_directory_add",
            "host_empty_dirs",
        }:
            assert (extra_root / "new_fixture/registered").exists() is (
                change == "user_directory_add"
            )
        catalogs = [
            (
                phase,
                snapshot_content(
                    next(
                        i
                        for i in reversed(request.items)
                        if isinstance(i, ContextItem) and i.key == "extensions.skills.catalog"
                    )
                ),
            )
            for phase, request in requests
        ]
        assert ("RELOAD_SKILL_MARKER" in catalogs[0][1]) is (
            change
            not in {
                "reenable",
                "feature_static_off",
                "package_reenable",
                "package_removed",
                "package_project",
            }
        )
        plugin_visible = change not in {
            "package_disabled",
            "package_project",
            "slow_host_catalog",
            "feature_static_off",
            "plugin",
            "bad_manifest",
            "remove",
            "host_catalog",
            "host_reconcile",
        }
        skill_visible = plugin_visible and change not in {"skill", "retired_apps"}
        plugin_context = next(
            (
                item
                for item in reversed(requests[-1][1].items)
                if isinstance(item, ContextItem) and item.key == "extensions.plugins.catalog"
            ),
            None,
        )
        assert (
            plugin_context is not None
            and "skills namespace: reload_fixture" in snapshot_content(plugin_context)
        ) is plugin_visible
        mcp_visible = plugin_visible or change in {
            "host_catalog",
            "host_reconcile",
            "slow_host_catalog",
        }
        mcp_visible = mcp_visible and change not in {"mcp_disabled", "mcp_allow", "mcp_deny"}
        assert plugin_callable is plugin_visible
        registrations = (
            (package / "registrations").read_text().splitlines()
            if (package / "registrations").exists()
            else []
        )
        assert len(registrations) == (
            0
            if change in {"feature_static_off", "package_project"}
            else 2
            if change in {"code", "code_failure"}
            else 1
        )
        closures = (
            (package / "closures").read_text().splitlines()
            if (package / "closures").exists()
            else []
        )
        assert len(closures) == len(registrations)
        if change not in {
            "reenable",
            "feature_static_off",
            "package_reenable",
            "package_removed",
            "package_project",
        }:
            assert any("echo version one" in item.content for item in observations), [
                item.content for item in observations
            ]
        if change == "code":
            assert any("echo version two" in item.content for item in observations)
        if change == "code_failure":
            assert not any("echo version two" in item.content for item in observations)
        if change == "bad_write":
            assert config.read_text() == "invalid=["
        else:
            assert (
                tomllib.loads(config.read_text())["mcp"]["servers"][trigger_name]["tools"]["write"][
                    "approval_mode"
                ]
                == "approve"
            )
        actual = {
            "model_skill": "RELOAD_SKILL_MARKER" in catalogs[-1][1],
            "tool_skill": any(
                "reload_fixture:guide" in i.content
                for i in observations
                if i.tool_name == "skill_list" or nested
            ),
            "plugin_rpc": ("docs", "read") in calls,
            "plugin_catalog": any(entry.server_name == "docs" for entry in catalog),
        }
        if change == "retired_apps":
            assert tomllib.loads(config.read_text())["apps"]["fixture"]["enabled"] == (
                "ignored legacy platform setting"
            )
            assert "apps" not in runtime._mcp_manager._approval_document
        if change in {"plugin", "skill"}:
            # The same saved configuration must work at cold startup. This
            # separates missing live publication from unsupported config syntax.
            cold_model = Model()
            cold_model.phase = 2
            cold_settings = replace(
                CorkiSettings.for_directory(tmp_path, config_file=config),
                execution_permissions=None,
                api_mode=settings.api_mode,
                model_contexts=settings.model_contexts,
                tool_search_mode=settings.tool_search_mode,
                tool_mode=settings.tool_mode,
            )
            cold = await LangGraphRuntime.acreate(
                settings=cold_settings,
                model=cold_model,
                registry=ToolRegistry(),
                database_path=tmp_path / "cold.db",
                home_path=home,
            )
            before_cold = len(calls)
            try:
                cold_events = [
                    event
                    async for event in cold.stream("list skills and search the plugin document")
                ]
                cold_catalog = await cold.mcp_tool_catalog()
            finally:
                await cold.aclose()
            assert isinstance(cold_events[-1], TurnCompleted), cold_events[-1]
            cold_skill = next(
                i
                for i in reversed(requests[-1][1].items)
                if isinstance(i, ContextItem) and i.key == "extensions.skills.catalog"
            )
            assert "RELOAD_SKILL_MARKER" not in snapshot_content(cold_skill)
            assert any(entry.server_name == "docs" for entry in cold_catalog) is (change == "skill")
            assert (("docs", "read") in calls[before_cold:]) is (change == "skill")
            assert all(client.is_closed for client in clients)
        assert actual == {
            "model_skill": skill_visible,
            "tool_skill": skill_visible,
            "plugin_rpc": mcp_visible,
            "plugin_catalog": mcp_visible,
        }

    asyncio.run(scenario())
