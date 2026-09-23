"""Native rule evaluation precedes real shell/Code Mode process admission."""

import asyncio
import json
import os
import shlex
import sys
from pathlib import Path

import pytest

from corki.config import CorkiSettings
from corki.config.exec_policy import ExecPolicySource
from corki.config.permissions import ExecutionPermissions
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools.builtin import process


def rule(prefix, decision):
    return f"prefix_rule(pattern={prefix!r}, decision={decision!r})"


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize(
    "command,rules,admitted,bypass",
    [
        ("rm -f fixture-target", (), False, False),
        ("env -i rm --force fixture-target", (), False, False),
        ("sudo rm -f fixture-target", (), False, False),
        ("trap 'rm -f fixture-target' EXIT", (), False, False),
        ("printf '%s' 'rm -f fixture-target'", (), True, False),
        ("rm -- -f fixture-target", (), True, False),
        ("printf fixture-target", (rule(["printf"], "forbidden"),), False, False),
        ("printf fixture-target", (rule(["printf"], "prompt"),), False, False),
        ("rm -f fixture-target", (rule(["rm", "-f"], "allow"),), True, True),
        (
            "rm -f fixture-target",
            (rule(["rm", "-f"], "allow"), rule(["rm"], "forbidden")),
            False,
            False,
        ),
        (
            "printf fixture-target",
            (
                rule(["printf"], "forbidden"),
                'prefix_rule(pattern=["printf", "fixture-target"], decision="forbidden", '
                'justification="USE_READONLY_ALTERNATIVE")',
            ),
            False,
            False,
        ),
        (
            "printf hi && rm -f fixture-target",
            (rule(["printf"], "allow"),),
            False,
            False,
        ),
        (
            "printf hi && rm -f fixture-target",
            (rule(["printf"], "allow"), rule(["rm", "-f"], "allow")),
            True,
            True,
        ),
        (
            "printf hi && printf fixture-target",
            (rule(["printf", "hi"], "allow"),),
            True,
            False,
        ),
        (
            "rm -f fixture-target",
            (rule(["rm", "-f"], "allow"), "not valid ( rules"),
            False,
            False,
        ),
    ],
)
def test_never_policy_before_spawn_in_actual_runtime(
    tmp_path, monkeypatch, mode, command, rules, admitted, bypass
):
    compiler = os.environ.get("CORKI_TEST_SANDBOX_COMPILER")
    if not compiler or sys.platform != "darwin":
        pytest.skip("requires explicit native compiler and macOS")

    async def scenario():
        requests, spawns = [], []
        sources = tuple(
            ExecPolicySource(str(tmp_path / f"{index}.rules"), text)
            for index, text in enumerate(rules)
        )

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    arguments = {"cmd": command, "login": False}
                    call = (
                        ToolCall(new_tool_call_id(), "exec_command", arguments)
                        if mode == "direct"
                        else ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            raw_arguments="text(await tools.exec_command("
                            + json.dumps(arguments)
                            + "))",
                            input_kind="freeform",
                        )
                    )
                    yield ModelCompleted((ToolCallItem(call, turn, step),))
                else:
                    yield ModelCompleted((AssistantMessageItem("handled", turn, step),))

            async def aclose(self):
                pass

        async def intercepted(command, **kwargs):
            spawns.append((command, kwargs["prepared_argv"]))
            raise OSError("fixture intercepted startup; command was not executed")

        monkeypatch.setattr(process, "_spawn", intercepted)
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                tool_mode=mode,
                execution_permissions=ExecutionPermissions(
                    Path(compiler),
                    tmp_path,
                    '{"type":"read-only"}',
                    exec_policy_sources=sources,
                ),
            ),
            database_path=tmp_path / "state.db",
            home_path=tmp_path / "home",
            model=Model(),
        )
        try:
            events = [e async for e in runtime.stream("test policy")]
            assert isinstance(events[-1], TurnCompleted), events
            assert len(requests) == 2
            assert len(spawns) == int(admitted)
            if admitted:
                assert spawns[0][0] == command
                assert ("sandbox-exec" not in spawns[0][1][0]) == bypass
            elif mode == "direct":
                results = [i for i in requests[-1].items if isinstance(i, ToolResultItem)]
                assert len(results) == 1 and results[0].is_error
                assert "policy" in results[0].content.lower()
                assert "fixture intercepted" not in results[0].content
                if any("USE_READONLY_ALTERNATIVE" in text for text in rules):
                    assert "USE_READONLY_ALTERNATIVE" in results[0].content
                if not rules and "rm -f" in command:
                    assert "rm -f style commands are not permitted" in results[0].content
            assert not runtime._process_manager._sessions
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("denied", [False, True])
@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_explicit_allow_cannot_bypass_managed_read_denial(tmp_path, mode, denied):
    from corki.config.managed_mcp import MCPRequirementsLayer, compose_mcp_requirements

    compiler = os.environ.get("CORKI_TEST_SANDBOX_COMPILER")
    if not compiler or sys.platform != "darwin":
        pytest.skip("requires explicit native compiler and macOS")

    async def scenario():
        secret = tmp_path / "private.txt"
        secret.write_text("CONTROLLED_FIXTURE_CONTENT")
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    arguments = {"cmd": "cat " + shlex.quote(str(secret)), "login": False}
                    call = (
                        ToolCall(new_tool_call_id(), "exec_command", arguments)
                        if mode == "direct"
                        else ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            raw_arguments="text(await tools.exec_command("
                            + json.dumps(arguments)
                            + "))",
                            input_kind="freeform",
                        )
                    )
                    yield ModelCompleted((ToolCallItem(call, turn, step),))
                else:
                    yield ModelCompleted((AssistantMessageItem("handled", turn, step),))

            async def aclose(self):
                pass

        requirements = compose_mcp_requirements(
            (
                MCPRequirementsLayer(
                    "host",
                    "[permissions.filesystem]\ndeny_read="
                    + json.dumps([str(secret)] if denied else []),
                ),
            )
        )
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                tool_mode=mode,
                execution_permissions=ExecutionPermissions(
                    Path(compiler),
                    tmp_path,
                    '{"type":"read-only"}',
                    exec_policy_sources=(
                        ExecPolicySource(str(tmp_path / "host.rules"), rule(["cat"], "allow")),
                    ),
                ),
            ),
            database_path=tmp_path / "state.db",
            home_path=tmp_path / "home",
            model=Model(),
            mcp_requirements=requirements,
        )
        try:
            assert isinstance(
                [e async for e in runtime.stream("test independent denial")][-1], TurnCompleted
            )
            assert len(requests) == 2
            output = "\n".join(
                i.content for i in requests[-1].items if isinstance(i, ToolResultItem)
            )
            assert ("CONTROLLED_FIXTURE_CONTENT" in output) is not denied
            assert secret.read_text() == "CONTROLLED_FIXTURE_CONTENT"
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
