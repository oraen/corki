import asyncio
import json
import os
import shlex
import sys

import pytest

from corki.code_mode.service import CodeModeService
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall


@pytest.mark.skipif(os.name == "nt", reason="POSIX command quoting and PTY")
@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("tty", [False, True])
@pytest.mark.parametrize(
    "policy_case", ["legacy", "canonical", "none", "core", "default", "bad_key"]
)
def test_runtime_shell_environment_policy(tmp_path, monkeypatch, mode, tty, policy_case):
    if mode != "direct" and not CodeModeService.available():
        pytest.skip("install corki[code-mode]")
    for name in ("CORKI_ENV_DROP", "CORKI_ENV_KEEP", "CORKI_ENV_KEY", "Node_Repl_Auth_Token"):
        monkeypatch.setenv(name, "fake-parent")
    for name in ("CORKI_ENV_RESTORE", "OUTSIDE_POLICY"):
        monkeypatch.delenv(name, raising=False)
    restricted = [
        "CODEX_EXEC_SERVER_NOISE_AUTH_TOKEN",
        "NODE_REPL_AUTH_TOKEN",
        "OPENAI_FEDERATION_RULE_ID",
        "OPENAI_IDENTITY_TOKEN_FILE",
        "OPENAI_WORKLOAD_IDENTITY_CONTEXT",
    ]
    restricted += [name.lower() for name in restricted]
    for name in restricted:
        monkeypatch.setenv(name, "fake-launch-context")
    config = tmp_path / "config.toml"
    policy_toml = (
        "[shell_environment_policy]\nignore_default_excludes = false\n"
        'exclude = ["CORKI_ENV_DROP", "CORKI_ENV_RESTORE"]\n'
        'include_only = ["CORKI_ENV_*", "*AUTH_TOKEN", "TERM", "PAGER"]\n'
        '[shell_environment_policy.set]\nCORKI_ENV_RESTORE = "restored"\n'
        'CORKI_ENV_KEY = "configured-key"\nOUTSIDE_POLICY = "excluded"\n'
        'NODE_REPL_AUTH_TOKEN = "fake-override"\nTERM = "wrong"\nPAGER = "wrong"\n'
    )
    expected = [None, "fake-parent", "configured-key", "restored", None, None, None, "dumb", "cat"]
    if policy_case == "canonical":
        policy_toml = (
            '[shell_environment_policy.filters]\n"CORKI_ENV_DROP" = "exclude"\n'
            '"CORKI_ENV_*" = "include"\n"*AUTH_TOKEN" = "include"\n'
            '[shell_environment_policy.set]\nCORKI_ENV_RESTORE = "restored"\n'
            'CORKI_ENV_KEY = "configured-key"\nOUTSIDE_POLICY = "excluded"\n'
            'NODE_REPL_AUTH_TOKEN = "fake-override"\n'
        )
    elif policy_case in ("none", "core"):
        policy_toml = f'[shell_environment_policy]\ninherit = "{policy_case}"\n'
        expected = [None] * 7 + ["dumb", "cat"]
    elif policy_case == "default":
        policy_toml = ""
        expected = ["fake-parent"] * 3 + [None] * 4 + ["dumb", "cat"]
    elif policy_case == "bad_key":
        policy_toml = '[shell_environment_policy.set]\n"INVALID=NAME" = "fake-value"\n'
    config.write_text("[skills]\nenabled = false\n" + f'[tools]\nmode = "{mode}"\n' + policy_toml)
    names = [
        "CORKI_ENV_DROP",
        "CORKI_ENV_KEEP",
        "CORKI_ENV_KEY",
        "CORKI_ENV_RESTORE",
        "OUTSIDE_POLICY",
        "Node_Repl_Auth_Token",
        "NODE_REPL_AUTH_TOKEN",
        "TERM",
        "PAGER",
    ]
    names.extend(restricted)
    expected.extend([None] * len(restricted))
    probe = f"import os,json; print('ENV_RESULT='+json.dumps([os.getenv(k) for k in {names!r}]))"
    arguments = {
        "cmd": shlex.join([sys.executable, "-I", "-S", "-c", probe]),
        "login": False,
        "tty": tty,
        "yield_time_ms": 1000,
    }

    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
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

        settings = CorkiSettings.for_directory(tmp_path, config_file=config)
        assert settings.tool_mode == mode
        runtime = await LangGraphRuntime.acreate(
            settings=settings, database_path=tmp_path / "sessions.db", model=Model()
        )
        try:
            events = [e async for e in runtime.stream("probe shell environment")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            result = next(i for i in requests[-1].items if isinstance(i, ToolResultItem))
            if policy_case == "bad_key":
                assert result.is_error, result.content
                assert "ENV_RESULT=" not in result.content
            else:
                assert not result.is_error, result.content
                line = next(
                    line for line in result.content.splitlines() if line.startswith("ENV_RESULT=")
                )
                assert json.loads(line.removeprefix("ENV_RESULT=")) == expected
            assert "fake-parent" not in repr(requests[0])
            assert not runtime._process_manager.list_background_terminals()
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.skipif(os.name == "nt", reason="POSIX command quoting and PTY")
@pytest.mark.parametrize("tty", [False, True])
def test_admitted_process_environment_is_frozen_before_startup_task(tmp_path, monkeypatch, tty):
    from corki.tools.builtin import process

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        original = process._spawn

        async def delayed(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        monkeypatch.setattr(process, "_spawn", delayed)
        monkeypatch.setenv("CORKI_ENV_FREEZE", "before")
        manager = process.ProcessManager()
        command = shlex.join(
            [
                sys.executable,
                "-I",
                "-S",
                "-c",
                "import os; print(os.getenv('CORKI_ENV_FREEZE'))",
            ]
        )
        task = asyncio.create_task(
            manager.execute(
                command,
                cwd=tmp_path,
                yield_seconds=1,
                tty=tty,
                login=False,
            )
        )
        try:
            await asyncio.wait_for(entered.wait(), 2)
            monkeypatch.setenv("CORKI_ENV_FREEZE", "after")
            release.set()
            first = await task
            second = await manager.execute(
                command, cwd=tmp_path, yield_seconds=1, tty=tty, login=False
            )
            assert (first.output.strip(), second.output.strip()) == ("before", "after")
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await manager.terminate_all()

    asyncio.run(scenario())
