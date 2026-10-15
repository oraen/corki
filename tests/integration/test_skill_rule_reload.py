"""Layer-backed skill rules publish at Turn admission across all tool-use modes."""

import asyncio
import json
import threading
import tomllib
from dataclasses import replace

import httpx
import pytest

from corki.config import CorkiSettings
from corki.config.skills import SkillRule
from corki.config.toml_edits import set_config_value
from corki.context.world_state import snapshot_content
from corki.core import LangGraphRuntime
from corki.mcp.client import HttpMCPClient
from corki.models import ModelCompleted
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


@pytest.mark.parametrize("mode", ["compatible", "native", "code_mode"])
@pytest.mark.parametrize(
    "change",
    [
        "skill",
        "enable",
        "remove",
        "host_enabled",
        "host_disabled",
        "unchanged",
        "bad_write",
        "failed_prepare",
        "failed_mcp_prepare",
        "failed_admission",
        "model_changed",
        "cancel_reload",
        "close_reload",
    ],
)
def test_skill_rule_reload_keeps_admitted_turn_and_updates_next_turn(
    tmp_path, monkeypatch, mode, change
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
                    "mcpServers": {"docs": {"type": "http", "url": "https://docs.invalid"}},
                }
            )
        )
        skill = package / "skills/guide/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(
            "---\nname: guide\ndescription: RELOAD_SKILL_MARKER\n---\nFixture guide.\n"
        )
        config = tmp_path / "config.toml"
        original = (
            '[mcp]\napproval_policy="on-request"\n'
            '[mcp.servers.reload_trigger]\ntransport="http"\nurl="https://trigger.invalid"\n'
        )
        initial_visible = change not in {"enable", "remove", "host_disabled"}
        initial_rule = (
            '[[skills.config]]\nname="reload_fixture:guide"\nenabled=false\n'
            if not initial_visible
            else ""
        )
        config.write_text(original + initial_rule)
        clients, calls, reviews, requests, observations = [], [], [], [], []
        nested = mode == "code_mode"

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
                    trigger = settings.name == "reload_trigger"
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
                    result = {"content": [{"type": "text", "text": "fixture result"}]}
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
                        "const l=ALL_TOOLS.find(t=>t.name.endsWith('skill_list'));"
                        "text(await tools[l.name]({}));"
                        "const r=ALL_TOOLS.find(t=>t.name.endsWith('skill_read'));"
                        "text(await tools[r.name]({name:'reload_fixture:guide'}));"
                    )
                    call = ToolCall(
                        new_tool_call_id(), "exec", None, input_kind="freeform", raw_arguments=code
                    )
                elif not nested:
                    search_step = 1 if self.phase == 1 else 3
                    if self.phase == 2 and self.steps == 1:
                        call = ToolCall(new_tool_call_id(), "skill_list", {})
                    elif self.phase == 2 and self.steps == 2 or self.phase == 1 and self.steps == 4:
                        call = ToolCall(
                            new_tool_call_id(), "skill_read", {"name": "reload_fixture:guide"}
                        )
                    elif self.phase == 1 and self.steps == 3:
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
                if call is not None:
                    yield ModelCompleted((ToolCallItem(call, turn, step),))
                else:
                    observations.extend(
                        (self.phase, i)
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
            model_contexts=(
                ModelContextInfo("gpt-5", supports_search_tool=True),
                ModelContextInfo("fixture-wide", 400_000, supports_search_tool=True),
            ),
            tool_search_mode="disabled" if nested else mode,
            tool_mode="code_mode_only" if nested else "direct",
        )
        if change in {"host_enabled", "host_disabled"}:
            settings = replace(
                settings,
                skills_config=(SkillRule(change == "host_enabled", name="reload_fixture:guide"),),
            )
        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            model=model,
            registry=ToolRegistry(),
            database_path=tmp_path / "runtime.db",
            home_path=home,
        )

        async def host(request):
            reviews.append(request)
            enabled = change in {"enable", "host_disabled"}
            extra = (
                '[[skills.config]]\nname="reload_fixture:guide"\nenabled='
                + ("true" if enabled else "false")
                + "\n"
                if change not in {"remove", "unchanged"}
                else ""
            )
            config.write_text("invalid=[" if change == "bad_write" else original + extra)
            if change in {"failed_prepare", "failed_mcp_prepare"}:

                def fail(*args, **kwargs):
                    raise ValueError("fixture preparation failed")

                target = runtime._skill_service if change == "failed_prepare" else runtime
                attribute = (
                    "with_configuration"
                    if change == "failed_prepare"
                    else "_prepare_mcp_configuration"
                )
                monkeypatch.setattr(target, attribute, fail)
            runtime.respond_mcp_elicitation(
                request.server_name, request.request_id, "accept", meta={"persist": "always"}
            )

        runtime.set_mcp_elicitation_handler(host)
        writing = asyncio.Event()
        release = threading.Event()
        owned_tasks = []
        interrupted = change in {"cancel_reload", "close_reload"}
        if interrupted:
            loop = asyncio.get_running_loop()

            def delayed_write(*args):
                loop.call_soon_threadsafe(writing.set)
                assert release.wait(10), "test must release the owned writer"
                set_config_value(*args)

            monkeypatch.setattr("corki.mcp.approval_persistence.set_config_value", delayed_write)

        async def collect():
            events = []
            try:
                async for event in runtime.stream("run the approval reload trigger"):
                    events.append(event)
            except asyncio.CancelledError:
                # Runtime yields the terminal and then propagates cancellation.
                # The fixture observes both contracts; production must not swallow it.
                if not interrupted:
                    raise
                assert isinstance(events[-1], TurnCancelled)
            return events

        try:
            if change == "model_changed":
                await runtime.update_thread_settings(model="fixture-wide")
            if interrupted:
                worker = asyncio.create_task(collect())
                owned_tasks.append(worker)
                await asyncio.wait_for(writing.wait(), 5)
                stopping = asyncio.create_task(
                    runtime.aclose() if change == "close_reload" else runtime.cancel_active()
                )
                owned_tasks.append(stopping)
                await asyncio.sleep(0.01)
                assert not worker.done() and not runtime._active_run.done.is_set()
                if change == "close_reload":
                    assert not stopping.done()
                else:
                    # Cancellation requests are non-blocking; the Turn still owns the writer.
                    await runtime.cancel_active()
                    await runtime.cancel_active()
                assert not calls and runtime._pending_skill_service is None
                release.set()
                first = await asyncio.wait_for(worker, 5)
                await asyncio.wait_for(stopping, 5)
                assert isinstance(first[-1], TurnCancelled)
                assert sum(isinstance(event, TurnCancelled) for event in first) == 1
                assert not calls
                assert (
                    tomllib.loads(config.read_text())["mcp"]["servers"]["reload_trigger"]["tools"][
                        "write"
                    ]["approval_mode"]
                    == "approve"
                )
                if change == "close_reload":
                    assert runtime._pending_skill_service is None
                    assert (
                        runtime._mcp_manager._approval_document.get("mcp", {})
                        .get("servers", {})
                        .get("reload_trigger", {})
                        .get("tools")
                        is None
                    )
                    assert (
                        runtime._mcp_manager._approval_persistence.document.get("mcp", {})
                        .get("servers", {})
                        .get("reload_trigger", {})
                        .get("tools")
                        is None
                    )
                    assert all(client.is_closed for client in clients)
                    return
            else:
                first = await collect()
            if change == "failed_admission":
                previous_service = runtime._skill_service
                previous_list = runtime._registry.get("skill_list")
                previous_graph = runtime._graph
                previous_items = await runtime._repository.load_items(runtime.thread_id)

                def fail_compile(*args, **kwargs):
                    raise ValueError("fixture compile failed")

                with monkeypatch.context() as patch:
                    patch.setattr(type(previous_graph), "compile", fail_compile)
                    with pytest.raises(ValueError, match="fixture compile failed"):
                        _ = [event async for event in runtime.stream("must not be persisted")]
                assert runtime._skill_service is previous_service
                assert runtime._registry.get("skill_list") is previous_list
                assert runtime._graph is previous_graph
                assert await runtime._repository.load_items(runtime.thread_id) == previous_items
                assert runtime._pending_skill_service is not None
            model.phase, model.steps = 2, 0
            second = [
                event
                async for event in runtime.stream("list skills and search the plugin document")
            ]
            catalog = await runtime.mcp_tool_catalog()
            if change == "model_changed":
                assert runtime._skill_service._catalog_budget.limit == 8000
        finally:
            release.set()
            await asyncio.gather(*owned_tasks, return_exceptions=True)
            await runtime.aclose()
        assert all(client.is_closed for client in clients)
        assert isinstance(
            first[-1], TurnCancelled if interrupted else TurnCompleted
        ) and isinstance(second[-1], TurnCompleted), (
            first[-1],
            second[-1],
        )
        assert len(reviews) == 1
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
        assert ("RELOAD_SKILL_MARKER" in catalogs[0][1]) is initial_visible
        assert all(
            ("RELOAD_SKILL_MARKER" in content) is initial_visible
            for phase, content in catalogs
            if phase == 1
        )
        previous_results = [item for phase, item in observations if phase == 1]
        if not interrupted:
            assert (
                any("Fixture guide." in item.content for item in previous_results)
                is initial_visible
            )
            assert (
                any(
                    '"description": "RELOAD_SKILL_MARKER"' in item.content
                    for item in previous_results
                )
                is initial_visible
            )
        observations = [item for phase, item in observations if phase == 2]
        skill_visible = change not in {
            "skill",
            "host_disabled",
            "failed_admission",
            "model_changed",
            "cancel_reload",
        }
        if change == "bad_write":
            assert config.read_text() == "invalid=["
        else:
            assert (
                tomllib.loads(config.read_text())["mcp"]["servers"]["reload_trigger"]["tools"][
                    "write"
                ]["approval_mode"]
                == "approve"
            )
        actual = {
            "model_skill": "RELOAD_SKILL_MARKER" in catalogs[-1][1],
            "tool_skill": any(
                '"description": "RELOAD_SKILL_MARKER"' in i.content
                for i in observations
                if i.tool_name == "skill_list" or nested
            ),
            "plugin_rpc": ("docs", "read") in calls,
            "plugin_catalog": any(entry.server_name == "docs" for entry in catalog),
        }
        assert any("Fixture guide." in item.content for item in observations) is skill_visible
        if not nested:
            read = next(item for item in observations if item.tool_name == "skill_read")
            assert read.is_error is not skill_visible
        if change in {"failed_prepare", "failed_mcp_prepare"}:
            # A successful disk write is not a successful in-memory reload.
            assert (
                runtime._mcp_manager._approval_persistence.document.get("mcp", {})
                .get("servers", {})
                .get("reload_trigger", {})
                .get("tools")
                is None
            )
            assert (
                runtime._mcp_manager._approval_document.get("mcp", {})
                .get("servers", {})
                .get("reload_trigger", {})
                .get("tools")
                is None
            )
        assert calls.count(("reload_trigger", "write")) == (0 if interrupted else 1)
        assert actual == {
            "model_skill": skill_visible,
            "tool_skill": skill_visible,
            "plugin_rpc": True,
            "plugin_catalog": True,
        }

    asyncio.run(scenario())
