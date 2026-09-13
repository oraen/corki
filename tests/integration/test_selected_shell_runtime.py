import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest

from corki.code_mode.service import CodeModeService
from corki.config import CorkiSettings
from corki.config.managed_mcp import MCPRequirementsLayer, compose_mcp_requirements
from corki.config.permissions import parse_execution_permissions
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall
from corki.sessions import TurnRecord, TurnStatus


@pytest.mark.skipif(os.name == "nt" or not Path("/bin/zsh").is_file(), reason="requires local zsh")
@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("change_policy", [False, True])
def test_cold_checkpoint_preserves_prepared_step_shell(tmp_path, monkeypatch, mode, change_policy):
    import pwd

    if mode != "direct" and not CodeModeService.available():
        pytest.skip("install corki[code-mode]")
    account = ["/bin/zsh"]
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: SimpleNamespace(pw_shell=account[0]))

    async def scenario():
        requests = []
        settings = CorkiSettings(working_directory=tmp_path, skills_enabled=False, tool_mode=mode)

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 2:
                    arguments = {"cmd": 'printf "%s" "${ZSH_VERSION-unselected}"', "login": False}
                    call = (
                        ToolCall(new_tool_call_id(), "exec_command", arguments)
                        if mode == "direct"
                        else ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            raw_arguments="text((await tools.exec_command("
                            + json.dumps(arguments)
                            + ")).output)",
                            input_kind="freeform",
                        )
                    )
                    item = ToolCallItem(call, turn, step)
                else:
                    item = AssistantMessageItem("done", turn, step)
                yield ModelCompleted((item,))

            async def aclose(self):
                pass

        class Sink:
            async def emit(self, event):
                pass

        def create(thread_id=None):
            return LangGraphRuntime.create(
                settings=settings,
                database_path=tmp_path / "sessions.db",
                model=Model(),
                thread_id=thread_id,
                mcp_requirements=compose_mcp_requirements(
                    (
                        MCPRequirementsLayer(
                            "host", 'additional_developer_instructions = "NEW POLICY"'
                        ),
                    )
                )
                if thread_id is not None and change_policy
                else None,
            )

        runtime = create()
        try:
            events = [e async for e in runtime.stream("first turn")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            thread, turn = runtime.thread_id, new_turn_id()
            user = UserMessageItem("prepared", turn)
            await runtime._repository.save_turn(
                TurnRecord(turn, thread, TurnStatus.RUNNING, user.content)
            )
            config = runtime._graph_config(turn)
            await runtime._compiled.ainvoke(
                _initial_state(thread, turn, settings, user),
                config=config,
                context=GraphRunContext(events=Sink()),
                interrupt_before=["call_model"],
            )
            assert (await runtime._compiled.aget_state(config)).next == ("call_model",)
            prepared = await runtime._repository.load_items(thread)
            await runtime.aclose()
            account[0] = "/bin/bash"
            runtime = create(thread)
            events = [e async for e in runtime.resume_pending()]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            visible = tuple(
                i for i in prepared if not isinstance(i, ContextItem) or not i.is_snapshot_only
            )
            # Ordinary replay preserves the frozen items without adding private metadata.
            expected_items = visible
            restored = requests[1].items
            if change_policy:
                policies = tuple(
                    i
                    for i in restored
                    if isinstance(i, ContextItem) and i.key == "managed_developer_instructions"
                )
                assert len(policies) == 1 and "NEW POLICY" in policies[0].content
                restored = tuple(i for i in restored if i not in policies)
            assert restored == expected_items
            assert all(
                item.response_item_metadata_json is None
                for item in visible
                if isinstance(item, AssistantMessageItem)
            )
            result = next(i for i in requests[-1].items if isinstance(i, ToolResultItem))
            assert not result.is_error and "unselected" not in result.content
            assert runtime._process_manager.shell.name == "bash"
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.skipif(os.name == "nt" or not Path("/bin/zsh").is_file(), reason="requires local zsh")
@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("tty", [False, True])
def test_runtime_context_and_nonlogin_exec_share_selected_shell(tmp_path, monkeypatch, mode, tty):
    import pwd

    if mode != "direct" and not CodeModeService.available():
        pytest.skip("install corki[code-mode]")

    monkeypatch.setattr(pwd, "getpwuid", lambda uid: SimpleNamespace(pw_shell="/bin/zsh"))
    monkeypatch.setenv("SHELL", "/bin/sh")

    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    monkeypatch.setenv("SHELL", "/bin/bash")
                    arguments = {
                        "cmd": 'printf "%s" "${ZSH_VERSION-unselected}"',
                        "login": False,
                        "tty": tty,
                    }
                    call = (
                        ToolCall(new_tool_call_id(), "exec_command", arguments)
                        if mode == "direct"
                        else ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            raw_arguments="text((await tools.exec_command("
                            + json.dumps(arguments)
                            + ")).output)",
                            input_kind="freeform",
                        )
                    )
                    item = ToolCallItem(call, turn, step)
                else:
                    item = AssistantMessageItem("done", turn, step)
                yield ModelCompleted((item,))

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, tool_mode=mode
            ),
            database_path=tmp_path / "sessions.db",
            model=Model(),
        )
        try:
            events = [e async for e in runtime.stream("execute")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            environments = [
                i
                for i in requests[-1].items
                if isinstance(i, ContextItem) and i.key == "environment.primary"
            ]
            assert len(environments) == 1
            assert ElementTree.fromstring(environments[0].content).findtext("shell") == "zsh"
            result = next(i for i in requests[-1].items if isinstance(i, ToolResultItem))
            assert not result.is_error and "unselected" not in result.content
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.skipif(os.name == "nt" or not Path("/bin/zsh").is_file(), reason="requires local zsh")
@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_per_call_shell_type_override_does_not_replace_session_selection(
    tmp_path, monkeypatch, mode
):
    import pwd

    if mode != "direct" and not CodeModeService.available():
        pytest.skip("install corki[code-mode]")
    account = ["/bin/zsh"]
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: SimpleNamespace(pw_shell=account[0]))
    untrusted = tmp_path / "bash"
    untrusted.write_text("#!/bin/sh\nprintf hijacked\n")
    untrusted.chmod(0o755)

    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) <= 2:
                    arguments = {"cmd": 'printf "%s" "${BASH_VERSION-unselected}"', "login": False}
                    if len(requests) == 1:
                        arguments["shell"] = str(untrusted)
                    else:
                        arguments["cmd"] = 'printf "%s" "${ZSH_VERSION-unselected}"'
                    call = (
                        ToolCall(new_tool_call_id(), "exec_command", arguments)
                        if mode == "direct"
                        else ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            raw_arguments="text((await tools.exec_command("
                            + json.dumps(arguments)
                            + ")).output)",
                            input_kind="freeform",
                        )
                    )
                    item = ToolCallItem(call, turn, step)
                else:
                    item = AssistantMessageItem("done", turn, step)
                yield ModelCompleted((item,))

            async def aclose(self):
                pass

        def create(thread_id=None):
            return LangGraphRuntime.create(
                settings=CorkiSettings(
                    working_directory=tmp_path, skills_enabled=False, tool_mode=mode
                ),
                database_path=tmp_path / "sessions.db",
                model=Model(),
                thread_id=thread_id,
            )

        runtime = create()
        try:
            events = [e async for e in runtime.stream("override then default")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            results = [i for i in requests[-1].items if isinstance(i, ToolResultItem)]
            assert len(results) == 2
            assert all(
                not i.is_error and "hijacked" not in i.content and "unselected" not in i.content
                for i in results
            )
            assert runtime._process_manager.shell.name == "zsh"
            account[0] = "/bin/bash"
            events = [e async for e in runtime.stream("same session new turn")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert runtime._process_manager.shell.name == "zsh"
            thread = runtime.thread_id
            await runtime.aclose()
            runtime = create(thread)
            events = [e async for e in runtime.stream("new session reselects")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert runtime._process_manager.shell.name == "bash"
            contexts = [
                i
                for i in requests[-1].items
                if isinstance(i, ContextItem) and i.key == "environment.primary"
            ]
            assert len(contexts) == 2
            assert ElementTree.fromstring(contexts[0].content).findtext("shell") == "zsh"
            assert ElementTree.fromstring(contexts[1].content).findtext("shell") == "bash"
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("login", [None, False, True])
def test_runtime_login_config_gate_precedes_process_spawn(tmp_path, monkeypatch, mode, login):
    from corki.tools.builtin import process

    if mode != "direct" and not CodeModeService.available():
        pytest.skip("install corki[code-mode]")
    original = process._spawn
    spawned = []

    async def spawn(*args, **kwargs):
        spawned.append(kwargs["login"])
        return await original(*args, **kwargs)

    monkeypatch.setattr(process, "_spawn", spawn)

    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    arguments = {"cmd": "echo OK"}
                    if login is not None:
                        arguments["login"] = login
                    call = (
                        ToolCall(new_tool_call_id(), "exec_command", arguments)
                        if mode == "direct"
                        else ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            raw_arguments="try {text((await tools.exec_command("
                            + json.dumps(arguments)
                            + ")).output)} catch(e) {text(String(e))}",
                            input_kind="freeform",
                        )
                    )
                    item = ToolCallItem(call, turn, step)
                else:
                    item = AssistantMessageItem("done", turn, step)
                yield ModelCompleted((item,))

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                tool_mode=mode,
                allow_login_shell=False,
            ),
            database_path=tmp_path / "sessions.db",
            model=Model(),
        )
        try:
            events = [e async for e in runtime.stream("execute")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            result = next(i for i in requests[-1].items if isinstance(i, ToolResultItem))
            assert spawned == ([] if login else [False])
            assert ("login shell is disabled by config" in result.content) == (login is True)
            if login is not True:
                assert "OK" in result.content
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "sandboxed",
    [
        False,
        pytest.param(
            True,
            marks=pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt exit status"),
        ),
    ],
)
def test_selected_executable_disappearing_returns_observation_without_shell_switch(
    tmp_path, monkeypatch, sandboxed
):
    from corki.shell import Shell, ShellType
    from corki.tools.builtin import process

    selected = Shell(ShellType.BASH, tmp_path / "missing-bash")
    monkeypatch.setattr(process, "default_user_shell", lambda: selected)

    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                item = (
                    ToolCallItem(
                        ToolCall(
                            new_tool_call_id(),
                            "exec_command",
                            {"cmd": "echo SHOULD_NOT_RUN", "login": False},
                        ),
                        turn,
                        step,
                    )
                    if len(requests) == 1
                    else AssistantMessageItem("handled", turn, step)
                )
                yield ModelCompleted((item,))

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                execution_permissions=parse_execution_permissions(
                    {
                        "profile": {"type": "workspace-write" if sandboxed else "disabled"},
                    },
                    tmp_path,
                    configuration={"approval_policy": "never"},
                ),
            ),
            database_path=tmp_path / "sessions.db",
            model=Model(),
        )
        try:
            events = [e async for e in runtime.stream("execute")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            result = next(i for i in requests[-1].items if isinstance(i, ToolResultItem))
            assert result.is_error is (not sandboxed)
            assert "missing-bash" in result.content
            if sandboxed:
                # Seatbelt itself starts successfully; execvp failure is its
                # ordinary exit status, not a failure to spawn sandbox-exec.
                assert "Process exited with code 71" in result.content
            assert "SHOULD_NOT_RUN" not in result.content
            assert runtime._process_manager.shell == selected
            assert not runtime._process_manager._sessions
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
