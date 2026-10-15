"""Local hook inputs use execution identity, not a provider/account identity."""

import asyncio
import json
import shlex
import sys
from pathlib import Path

import pytest

from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.config.permissions import ExecutionPermissions
from corki.core import LangGraphRuntime
from corki.core.stop_hooks import command_identity
from corki.execution.bundled import bundled_compiler
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import SessionId, ThreadId, new_turn_id
from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id
from corki.protocol.session_source import (
    DEFAULT_SESSION_SOURCE,
    SessionSource,
    SubAgentSource,
    ThreadSpawnSource,
)


@pytest.mark.parametrize(
    "event_name,parent_exists",
    [
        ("Stop", False),
        ("SubagentStop", False),
        ("SubagentStop", True),
    ],
)
@pytest.mark.parametrize("policy", ["never", "on-request"])
@pytest.mark.parametrize("asynchronous", [False, True])
def test_hook_input_uses_persisted_session_and_effective_policy(
    tmp_path, event_name, parent_exists, policy, asynchronous
):
    async def scenario():
        command = shlex.join(
            [
                sys.executable,
                "-c",
                "import json,sys; from pathlib import Path; p=json.load(sys.stdin); "
                "Path('input.json').write_text(json.dumps(p)); "
                "Path('read-transcripts.json').write_text(json.dumps({"
                "k: Path(p[k]).read_text() for k in ('transcript_path','agent_transcript_path') "
                "if p.get(k)})); print('{}')",
            ]
        )
        fingerprint, _ = command_identity(
            {"type": "command", "command": command, "async": asynchronous}, event_name=event_name
        )
        path = tmp_path / "config.toml"
        label = "stop" if event_name == "Stop" else "subagent_stop"
        key = f"{path}:{label}:0:0"
        contents = (
            f"[[hooks.{event_name}]]\n[[hooks.{event_name}.hooks]]\ntype='command'\n"
            f"command={json.dumps(command)}\nasync={json.dumps(asynchronous)}\n"
            f"[hooks.state.{json.dumps(key)}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )

        class Model:
            async def stream(self, request):
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                plugins_enabled=False,
                configuration=LocalConfigState((ConfigLayer(path, "user", contents=contents),)),
                execution_permissions=ExecutionPermissions(
                    bundled_compiler(),
                    tmp_path,
                    '{"type":"danger-full-access"}',
                    approval_policy_json=json.dumps(policy),
                ),
            ),
            model=Model(),
            database_path=tmp_path / "session.db",
            home_path=tmp_path / "home",
            session_id=SessionId("explicit-execution-session"),
            session_source=(
                DEFAULT_SESSION_SOURCE
                if event_name == "Stop"
                else SessionSource.subagent(
                    SubAgentSource("thread_spawn", ThreadSpawnSource(ThreadId("parent"), 1))
                )
            ),
        )
        try:
            if parent_exists:
                await runtime._ensure_ready()
                await runtime._repository.create_thread(ThreadId("parent"), tmp_path)
                await runtime._repository.append_items(
                    ThreadId("parent"), (UserMessageItem("PARENT ONLY", new_turn_id()),)
                )
            events = [event async for event in runtime.stream("finish")]
            assert isinstance(events[-1], TurnCompleted)
            async with asyncio.timeout(5):
                while runtime._graph._stop_hooks._async._tasks:
                    await asyncio.sleep(0.01)
            payload = json.loads((tmp_path / "input.json").read_text())
            assert payload["session_id"] == runtime.session_id == "explicit-execution-session"
            assert payload["session_id"] != runtime.thread_id
            assert payload["permission_mode"] == (
                "bypassPermissions" if policy == "never" else "default"
            )
            assert payload["hook_event_name"] == event_name
            if event_name == "SubagentStop":
                assert payload["agent_id"] == runtime.thread_id
                assert (payload["transcript_path"] is not None) == parent_exists
                transcript = Path(payload["agent_transcript_path"])
            else:
                assert "agent_id" not in payload
                transcript = Path(payload["transcript_path"])
            rows = [json.loads(line) for line in transcript.read_text().splitlines()]
            assert rows[0]["thread_id"] == runtime.thread_id
            assert rows[0]["session_id"] == runtime.session_id
            assert any(row.get("payload", {}).get("content") == "finish" for row in rows)
            read = json.loads((tmp_path / "read-transcripts.json").read_text())
            own = "agent_transcript_path" if event_name == "SubagentStop" else "transcript_path"
            assert "finish" in read[own] and "PARENT ONLY" not in read[own]
            if parent_exists:
                assert "PARENT ONLY" in read["transcript_path"]
                assert "finish" not in read["transcript_path"]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
