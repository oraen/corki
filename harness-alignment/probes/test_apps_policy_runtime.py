"""Native Apps policy must govern discovery and exact prepared-call admission.

Counterexamples kept outside the release until policy integration is implemented.
Scripted calls prove the harness contract, not model selection quality.
"""

import asyncio
import json
import sqlite3
from copy import deepcopy
from dataclasses import replace

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import HttpMCPClient
from corki.models import ModelCompleted
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry

# Native connectors/src/app_tool_policy.rs and core/src/mcp_tool_exposure.rs.
CASES = {
    "unconfigured": "",
    "app_off": "[apps.mail]\nenabled=false\n",
    "global_off": "[apps._default]\nenabled=false\n",
    "tool_off": "[apps.mail.tools.Gmail_Read]\nenabled=false\n",
    "title_off": "[apps.mail.tools.Read]\nenabled=false\n",
    "default_tools_off": "[apps.mail]\ndefault_tools_enabled=false\n",
    "destructive_off": "[apps._default]\ndestructive_enabled=false\n",
    "missing_destructive": "[apps._default]\ndestructive_enabled=false\n",
    "open_world_off": "[apps.mail]\nopen_world_enabled=false\n",
    "missing_open_world": "[apps.mail]\nopen_world_enabled=false\n",
    "tool_enable_over_hint": (
        "[apps.mail]\ndestructive_enabled=false\n[apps.mail.tools.Gmail_Read]\nenabled=true\n"
    ),
    "default_enable_over_hint": (
        "[apps.mail]\ndestructive_enabled=false\ndefault_tools_enabled=true\n"
    ),
    "app_off_over_tool": (
        "[apps.mail]\nenabled=false\n[apps.mail.tools.Gmail_Read]\nenabled=true\n"
    ),
    "partial_app_over_global": (
        "[apps._default]\nenabled=false\n[apps.mail]\ndefault_tools_enabled=true\n"
    ),
    "raw_entry_over_title": (
        "[apps.mail.tools.Gmail_Read]\n[apps.mail.tools.Read]\nenabled=false\n"
    ),
    "ordinary": "[apps._default]\nenabled=false\n",
    "missing_connector": "",
    "same_raw_disabled": "[apps.zzz]\nenabled=false\n",
    "approval_global": '[apps._default]\ndefault_tools_approval_mode="prompt"\n',
    "approval_app": '[apps.mail]\ndefault_tools_approval_mode="prompt"\n',
    "approval_raw": '[apps.mail.tools.Gmail_Read]\napproval_mode="prompt"\n',
    "approval_title": '[apps.mail.tools.Read]\napproval_mode="prompt"\n',
    "approval_link": '[apps.mail.links.account]\ndefault_tools_approval_mode="prompt"\n',
    "approval_tool_over_link": (
        '[apps.mail.links.account]\ndefault_tools_approval_mode="prompt"\n'
        '[apps.mail.tools.Gmail_Read]\napproval_mode="approve"\n'
    ),
}
HIDDEN = {
    "app_off",
    "global_off",
    "tool_off",
    "title_off",
    "default_tools_off",
    "destructive_off",
    "missing_destructive",
    "open_world_off",
    "missing_open_world",
    "app_off_over_tool",
    "missing_connector",
}


@pytest.mark.parametrize("mode", ["compatible", "native", "code_mode"])
@pytest.mark.parametrize("case", CASES)
def test_apps_policy_reaches_discovery_and_call_admission(tmp_path, monkeypatch, mode, case):
    async def scenario():
        calls, clients, prompts, outputs, discovered = [], [], [], [], []
        server = "ordinary" if case == "ordinary" else "codex_apps"
        definition = {
            "name": "Gmail_Read",
            "title": "Gmail_Read",
            "description": "Apps policy fixture",
            "inputSchema": {"type": "object"},
            "annotations": {
                "readOnlyHint": True,
                "destructiveHint": False,
                "openWorldHint": False,
            },
            "_meta": {
                "connector_id": "mail",
                "connector_name": "Gmail",
                "link_id": "account",
            },
        }
        if case in ("destructive_off", "tool_enable_over_hint", "default_enable_over_hint"):
            definition["annotations"]["destructiveHint"] = True
        if case == "open_world_off":
            definition["annotations"]["openWorldHint"] = True
        if case == "missing_destructive":
            del definition["annotations"]["destructiveHint"]
        if case == "missing_open_world":
            del definition["annotations"]["openWorldHint"]
        if case == "missing_connector":
            del definition["_meta"]["connector_id"]
        definitions = [definition]
        if case == "same_raw_disabled":
            shadow = deepcopy(definition)
            shadow["_meta"]["connector_id"] = "zzz"
            definitions.append(shadow)

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
                    result = {"tools": definitions}
                else:
                    assert packet["method"] == "tools/call"
                    calls.append(packet["params"])
                    result = {"content": [{"type": "text", "text": "business result"}]}
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": packet["id"], "result": result}
                )

            client = HttpMCPClient(settings, transport=httpx.MockTransport(respond))
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)

        class Model:
            steps = 0

            async def stream(self, request):
                self.steps += 1
                turn, step = request.items[-1].turn_id, new_step_id()
                if self.steps == 1 and mode != "code_mode":
                    call = ToolCall(
                        new_tool_call_id(), "tool_search", {"query": "Apps policy fixture"}
                    )
                elif self.steps == (1 if mode == "code_mode" else 2):
                    if mode == "code_mode":
                        call = ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments="const t = ALL_TOOLS.find("
                            't => t.description.includes("Apps policy fixture"));'
                            'if (t) { text("policy:present"); text(await tools[t.name]({})); }'
                            'else { text("policy:absent"); }',
                        )
                    else:
                        found = [
                            tool
                            for item in request.items
                            for tool in getattr(item, "discovered_tools", ())
                        ]
                        discovered.extend(found)
                        if not found:
                            yield ModelCompleted((AssistantMessageItem("no tool", turn, step),))
                            return
                        call = ToolCall(new_tool_call_id(), found[0].name, {})
                else:
                    outputs.append([i for i in request.items if isinstance(i, ToolResultItem)][-1])
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, step),))

            async def aclose(self):
                pass

        config = tmp_path / "policy.toml"
        config.write_text(CASES[case])
        settings = replace(
            CorkiSettings.for_directory(tmp_path, config_file=config),
            skills_enabled=False,
            execution_permissions=None,
            mcp_servers=(MCPServerSettings(server, "http", url="https://fixture.invalid"),),
            mcp_approval_policy="on-request" if case.startswith("approval_") else "never",
            api_mode="responses",
            model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
            tool_search_mode="disabled" if mode == "code_mode" else mode,
            tool_mode="code_mode_only" if mode == "code_mode" else "direct",
        )
        runtime = LangGraphRuntime.create(
            settings=settings,
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "policy.db",
            home_path=tmp_path / "home",
        )

        async def host(request):
            prompts.append(request)
            runtime.respond_mcp_elicitation(request.server_name, request.request_id, "decline")

        runtime.set_mcp_elicitation_handler(host)
        try:
            events = [event async for event in runtime.stream("use the policy fixture")]
        finally:
            await runtime.aclose()
        assert all(client.is_closed for client in clients)
        assert isinstance(events[-1], TurnCompleted), events[-1]
        exposed = case not in HIDDEN
        prompt = case.startswith("approval_") and case != "approval_tool_over_link"
        denied = prompt or case == "same_raw_disabled"
        found = "policy:present" in outputs[0].content if mode == "code_mode" else bool(discovered)
        with sqlite3.connect(tmp_path / "policy.db") as db:
            rows = db.execute(
                "SELECT status,result_json FROM tool_executions WHERE tool_name LIKE 'mcp__%'"
            ).fetchall()
        actual = (found, len(calls), len(prompts), len(rows))
        expected = (exposed, int(exposed and not denied), int(prompt), int(exposed))
        assert actual == expected, (case, actual, expected)
        if exposed:
            assert rows[0][0] == "completed"
            result = json.loads(rows[0][1])
            assert result["is_error"] == denied and not result.get("dispatch_error", False)
            assert outputs[0].is_error == (denied and mode != "code_mode")
            if case == "same_raw_disabled":
                assert "blocked by app configuration" in outputs[0].content

    asyncio.run(scenario())
