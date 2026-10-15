"""Shell Hook command rewrites preserve options and still require real approval."""

import asyncio
import json
import shlex
import sys
from dataclasses import replace

import pytest
from test_bundled_execution import compiler as compiler
from test_execution_approvals import Model

from corki.config import CorkiSettings
from corki.config.exec_policy import ExecPolicySource
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.config.permissions import ExecutionPermissions
from corki.core import LangGraphRuntime
from corki.core.stop_hooks import command_identity
from corki.protocol.events import HookCompleted, TurnCompleted
from corki.protocol.items import ToolCallItem, ToolResultItem


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize(
    "action",
    [
        "rewrite",
        "invalid",
        "block",
        "decline",
        "bad_tty",
        "bad_login",
        "bad_yield",
        "bad_prefix",
        "bad_permission",
        "bad_max_bool",
        "bad_max_overflow",
        "late_workdir",
        "late_null",
    ],
)
def test_shell_hook_rewrite_preserves_options_and_approval(
    tmp_path, compiler, monkeypatch, mode, action
):
    async def scenario():
        workdir = tmp_path / "work"
        workdir.mkdir()
        original, revised = "printf ORIGINAL_COMMAND", "printf REVISED_COMMAND"
        options = {
            "workdir": str(workdir),
            "tty": False,
            "yield_time_ms": 1000,
            "max_output_tokens": 200,
        }
        model = Model(mode, original, options)
        invalid_options = {
            "bad_tty": ("tty", "yes"),
            "bad_login": ("login", 1),
            "bad_yield": ("yield_time_ms", -1),
            "bad_prefix": ("prefix_rule", [123]),
            "bad_permission": ("sandbox_permissions", "invalid"),
            "bad_max_bool": ("max_output_tokens", True),
            "bad_max_overflow": ("max_output_tokens", 1 << 64),
            "late_workdir": ("workdir", 123),
            "late_null": ("login", None),
        }
        if action in invalid_options:
            key, value = invalid_options[action]
            options[key] = value
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "updatedInput": {
                    "command": 123 if action == "invalid" else revised,
                    "workdir": "/not-the-admitted-directory",
                    "tty": True,
                },
            }
        }
        if action == "block":
            output = {"decision": "block", "reason": "shell denied"}
        command = shlex.join(
            [
                sys.executable,
                "-c",
                "import json,sys; from pathlib import Path; "
                "p=json.load(sys.stdin); Path('shell-hook.json').write_text(json.dumps(p)); "
                f"print(json.dumps({output!r}))",
            ]
        )
        source = tmp_path / "config.toml"
        fingerprint = command_identity(
            {"type": "command", "command": command}, event_name="PreToolUse", matcher="Bash"
        )[0]
        definition = (
            "[[hooks.PreToolUse]]\nmatcher='Bash'\n"
            f"[[hooks.PreToolUse.hooks]]\ntype='command'\ncommand={json.dumps(command)}\n"
            f"[hooks.state.{json.dumps(f'{source}:pre_tool_use:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        policy = replace(
            ExecutionPermissions(compiler, tmp_path, '{"type":"read-only"}'),
            approval_policy_json='"on-request"',
            exec_policy_sources=(
                ExecPolicySource(
                    str(tmp_path / "host.rules"),
                    'prefix_rule(pattern=["printf"], decision="prompt")',
                ),
            ),
        )
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                plugins_enabled=False,
                tool_mode=mode,
                execution_permissions=policy,
                configuration=LocalConfigState((ConfigLayer(source, "user", contents=definition),)),
            ),
            model=model,
            home_path=tmp_path / "host",
            database_path=tmp_path / "state.db",
        )
        prompts, executions = [], []
        execute = runtime._process_manager.execute

        async def capture(command, **kwargs):
            executions.append(command)
            assert command == revised
            assert kwargs["cwd"] == workdir and kwargs["login"] is False
            assert kwargs["tty"] is False and kwargs["yield_seconds"] == 1
            return await execute(command, **kwargs)

        monkeypatch.setattr(runtime._process_manager, "execute", capture)

        async def respond(request):
            prompts.append(request)
            params = request.params["_meta"]["tool_params"]
            assert revised in params["argv"]
            assert params["cwd"] == str(workdir) and params["tty"] is False
            runtime.respond_execution_approval(
                request.request_id, "decline" if action == "decline" else "accept"
            )

        runtime.set_execution_approval_handler(respond)
        try:
            events = [event async for event in runtime.stream("execute command")]
            assert sum(isinstance(event, TurnCompleted) for event in events) == 1
            if action.startswith("bad_"):
                assert not any(isinstance(event, HookCompleted) for event in events)
                assert not (tmp_path / "shell-hook.json").exists()
                assert not executions and not prompts
                result = [
                    item for item in model.requests[-1].items if isinstance(item, ToolResultItem)
                ][-1]
                assert result.is_error
                return
            assert sum(isinstance(event, HookCompleted) for event in events) == 1
            payload = json.loads((tmp_path / "shell-hook.json").read_text())
            assert payload["tool_name"] == "Bash"
            assert payload["tool_input"] == {"command": original}
            expected = 1 if action in {"rewrite", "decline"} else 0
            assert len(prompts) == len(executions) == expected
            result = [
                item for item in model.requests[-1].items if isinstance(item, ToolResultItem)
            ][-1]
            if action == "rewrite":
                assert "REVISED_COMMAND" in result.content and not result.is_error
            else:
                assert "REVISED_COMMAND" not in result.content
            history = await runtime._repository.load_items(runtime.thread_id)
            outcomes = await runtime._repository.load_turn_tool_outcomes(
                runtime.thread_id, events[-1].turn_id
            )
            executed = next(item for item in outcomes if item.tool_name == "exec_command")
            if action == "rewrite":
                post = json.loads(executed.post_tool_use_json)
                assert post["tool_input"] == {"command": revised}
                assert post["tool_use_id"] == str(executed.call_id)
            else:
                assert executed.post_tool_use_json is None
            if mode == "direct":
                call = next(item.call for item in history if isinstance(item, ToolCallItem))
                assert call.arguments == {"cmd": original, "login": False, **options}
                assert result.is_error == (action != "rewrite")
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
