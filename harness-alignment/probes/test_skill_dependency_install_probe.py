"""Host-compatible installation path and its real Runtime safety controls.

Native references: core/src/mcp_skill_dependencies.rs and session/turn.rs.
An unrestricted Corki host is the compatibility counterpart, not a forged Codex
originator. Only fake stdio clients run; all configuration is temporary.
"""

import asyncio
import json
import tomllib

import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.config.mcp_requirements import MCPRequirements
from corki.config.permissions import ExecutionPermissions
from corki.core import LangGraphRuntime
from corki.execution.bundled import bundled_compiler
from corki.mcp.client import MCPClient
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import ToolCallId
from corki.protocol.items import ContextItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "configured",
        "unmentioned",
        "skills_disabled",
        "feature_disabled",
        "approved",
        "declined",
        "policy_changed",
        "write_failed",
        "managed_full",
        "managed_root",
        "managed_narrow",
    ],
)
def test_selected_skill_dependency_becomes_usable_across_turns(tmp_path, monkeypatch, case):
    async def scenario():
        skill = tmp_path / ".corki/skills/dependency-guide/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(
            "---\nname: dependency-guide\ndescription: fixture\n---\nINSTALL_PROBE_BODY"
        )
        policy = skill.parent / "agents/openai.yaml"
        policy.parent.mkdir()
        policy.write_text(
            "dependencies:\n  tools:\n    - type: mcp\n      value: dependency_probe\n"
            "      transport: stdio\n      command: corki-fake-dependency-command\n"
        )
        home = tmp_path / "isolated-home"
        clients, requests, calls = [], [], []
        prompts = []
        phase = 0

        class Client(MCPClient):
            def __init__(self, settings):
                super().__init__(settings)
                self.closed = False
                clients.append(self)

            async def start(self):
                pass

            async def list_tools(self):
                return [{"name": "lookup", "inputSchema": {"type": "object"}}]

            async def call_tool(self, name, arguments):
                calls.append((name, arguments))
                return {"content": [{"type": "text", "text": "DEPENDENCY_PROBE_RESULT"}]}

            async def aclose(self):
                self.closed = True

            async def _exchange(self, message):
                raise AssertionError(message)

            async def _send_notification(self, message):
                raise AssertionError(message)

        monkeypatch.setattr("corki.mcp.manager.create_client", lambda s: Client(s))
        monkeypatch.setattr("corki.mcp.manager.HttpMCPClient", lambda s, **kw: Client(s))

        class Model:
            async def stream(self, request):
                requests.append((phase, request))
                name = "mcp__dependency_probe::lookup"
                if phase == 1 and not calls and any(t.name == name for t in request.tools):
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(ToolCallId("dependency-probe-call"), name, {}),
                                request.items[-1].turn_id,
                                new_step_id(),
                            ),
                        )
                    )
                else:
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        configured = MCPServerSettings(
            "dependency_probe", "stdio", command="corki-fake-dependency-command"
        )
        permissions = None
        if case in {"managed_full", "managed_root", "managed_narrow"}:
            file_system = {"type": "unrestricted"}
            if case in {"managed_root", "managed_narrow"}:
                file_system = {
                    "type": "restricted",
                    "entries": [
                        {"access": "write", "path": {"type": "special", "value": {"kind": "root"}}},
                    ],
                }
                if case == "managed_narrow":
                    file_system["entries"].append(
                        {
                            "access": "read",
                            "path": {"type": "path", "path": str(tmp_path / "read-only")},
                        }
                    )
            permissions = ExecutionPermissions(
                bundled_compiler(),
                tmp_path,
                json.dumps(
                    {"type": "managed", "file_system": file_system, "network": "restricted"}
                ),
            )
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                tmp_path,
                execution_permissions=permissions,
                tool_search_mode="disabled",
                skills_enabled=case != "skills_disabled",
                mcp_servers=(configured,) if case == "configured" else (),
                mcp_approval_policy="on-request"
                if case in {"approved", "declined", "policy_changed"}
                else "never",
                **({"skill_mcp_dependency_install": False} if case == "feature_disabled" else {}),
            ),
            model=Model(),
            registry=ToolRegistry(),
            home_path=home,
            database_path=tmp_path / "session.db",
        )

        async def review(request):
            # A later tool-call approval remains a different kind of request.
            if request.kind == "skill_dependency_install":
                prompts.append(request)
                if case == "policy_changed":
                    runtime._mcp_manager._requirements = MCPRequirements(servers=())
            runtime.respond_mcp_elicitation(
                request.server_name,
                request.request_id,
                "decline" if case == "declined" else "accept",
            )

        runtime.set_mcp_elicitation_handler(review)
        if case == "write_failed":

            def fail_write(*args):
                raise PermissionError("fixture")

            monkeypatch.setattr("corki.skills.mcp_dependencies.add_missing_mcp_servers", fail_write)
        try:
            text = "hello" if case == "unmentioned" else "$dependency-guide"
            first = [event async for event in runtime.stream(text)]
            assert isinstance(first[-1], TurnCompleted)
            bodies = [
                item
                for _, request in requests
                for item in request.items
                if isinstance(item, ContextItem)
                and item.key.startswith("extensions.skills.selected.")
                and "INSTALL_PROBE_BODY" in item.content
            ]
            assert bool(bodies) == (case not in {"unmentioned", "skills_disabled"})
            phase = 1
            second = [
                event
                async for event in runtime.stream(
                    "$dependency-guide"
                    if case in {"approved", "declined", "policy_changed"}
                    else "Continue with available tools"
                )
            ]
            assert isinstance(second[-1], TurnCompleted)
            expected = case in {"missing", "configured", "approved", "managed_full", "managed_root"}
            assert bool(clients) == expected, "selected missing dependency was never started"
            assert bool(calls) == expected, "dependency never became model-callable"
            observations = [
                item
                for turn, request in requests
                if turn == 1
                for item in request.items
                if isinstance(item, ToolResultItem) and "DEPENDENCY_PROBE_RESULT" in item.content
            ]
            assert bool(observations) == expected
            path = home / "config.toml"
            document = tomllib.loads(path.read_text()) if path.exists() else {}
            installed = document.get("mcp", {}).get("servers", {}).get("dependency_probe")
            if case in {"missing", "approved", "managed_full", "managed_root"}:
                assert installed is not None, "installation must survive Runtime shutdown"
                assert installed["command"] == "corki-fake-dependency-command"
            else:
                assert installed is None, "controls must not write an installation"
            assert len(prompts) == (1 if case in {"approved", "declined", "policy_changed"} else 0)
        finally:
            await runtime.aclose()
        assert all(client.closed for client in clients)

    asyncio.run(asyncio.wait_for(scenario(), 10))
