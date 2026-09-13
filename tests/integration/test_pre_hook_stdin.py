"""An existing terminal continuation must not run a second PreToolUse hook."""

import asyncio
import json
import shlex
import sys

import pytest
from test_bundled_execution import compiler as compiler
from test_stdin_approval import Model, call

from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.config.permissions import ExecutionPermissions
from corki.core import LangGraphRuntime
from corki.core.stop_hooks import command_identity


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("poll", [False, True])
def test_real_terminal_continuation_skips_pre_hook(tmp_path, compiler, mode, poll):
    async def scenario():
        command = shlex.join(
            [
                sys.executable,
                "-c",
                "import json,sys; from pathlib import Path; p=json.load(sys.stdin); "
                "f=Path('stdin-hooks.jsonl'); "
                "f.open('a').write(json.dumps(p)+'\\n'); "
                "print(json.dumps({} if p['tool_name']=='Bash' else "
                "{'decision':'block','reason':'unexpected second pre hook'}))",
            ]
        )
        source = tmp_path / "config.toml"
        # Match Bash and write_stdin but not the outer Code Mode exec wrapper.
        matcher = "Bash|write_stdin"
        fingerprint = command_identity(
            {"type": "command", "command": command}, event_name="PreToolUse", matcher=matcher
        )[0]
        definition = (
            f"[[hooks.PreToolUse]]\nmatcher={json.dumps(matcher)}\n"
            f"[[hooks.PreToolUse.hooks]]\ntype='command'\ncommand={json.dumps(command)}\n"
            f"[hooks.state.{json.dumps(f'{source}:pre_tool_use:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        model = Model(mode)
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                plugins_enabled=False,
                tool_mode=mode,
                execution_permissions=ExecutionPermissions(
                    compiler, tmp_path, '{"type":"read-only"}'
                ),
                configuration=LocalConfigState((ConfigLayer(source, "user", contents=definition),)),
            ),
            model=model,
            home_path=tmp_path / "host",
            database_path=tmp_path / "state.db",
        )
        try:
            script = (
                "sleep 1; printf POLL_DONE" if poll else "read line; printf 'INPUT:%s' \"$line\""
            )
            await call(
                runtime,
                model,
                "exec_command",
                {
                    "cmd": script,
                    "tty": True,
                    "login": False,
                    "yield_time_ms": 250,
                },
            )
            assert len(runtime._process_manager._sessions) == 1
            session_id = next(iter(runtime._process_manager._sessions))
            origin = runtime._process_manager._sessions[session_id].terminal_info
            initial_outcomes = await runtime._repository.load_turn_tool_outcomes(
                runtime.thread_id, model.requests[-1].items[-1].turn_id
            )
            assert (
                next(
                    item for item in initial_outcomes if item.tool_name == "exec_command"
                ).post_tool_use_json
                is None
            )
            result = await call(
                runtime,
                model,
                "write_stdin",
                {
                    "session_id": str(session_id),
                    "chars": "" if poll else "hello\n",
                    "yield_time_ms": 1000,
                },
            )
            assert ("POLL_DONE" if poll else "INPUT:hello") in result.content
            assert "unexpected second pre hook" not in result.content
            payloads = [
                json.loads(line)
                for line in (tmp_path / "stdin-hooks.jsonl").read_text().splitlines()
            ]
            assert len(payloads) == 1 and payloads[0]["tool_name"] == "Bash"
            assert payloads[0]["tool_input"] == {"command": script}
            outcomes = await runtime._repository.load_turn_tool_outcomes(
                runtime.thread_id, model.requests[-1].items[-1].turn_id
            )
            completed = next(item for item in outcomes if item.tool_name == "write_stdin")
            post = json.loads(completed.post_tool_use_json)
            assert post["tool_name"] == "Bash"
            assert post["tool_use_id"] == origin.item_id
            assert post["tool_use_id"] != str(completed.call_id)
            assert post["tool_input"] == {"command": script}
            assert ("POLL_DONE" if poll else "INPUT:hello") in post["tool_response"]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
