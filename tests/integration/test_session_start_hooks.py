"""Session start hooks precede admission, not every ordinary user turn."""

import asyncio
import json
import shlex
import sys
from dataclasses import replace
from hashlib import sha256

import pytest

from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_thread_id
from corki.protocol.items import UserMessageItem
from corki.tools import ToolRegistry


@pytest.mark.parametrize(
    "source_kind", ["startup", "explicit_new", "resume", "resume_empty", "clear"]
)
@pytest.mark.parametrize("stop", [False, True])
@pytest.mark.parametrize("creation_fault", [None, "before_commit", "after_commit"])
def test_session_start_precedes_first_input(
    tmp_path, source_kind, stop, creation_fault, monkeypatch
):
    async def scenario():
        output = json.dumps(
            {
                "continue": not stop,
                "stopReason": "START_STOP",
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": "START_CONTEXT",
                },
            }
        )
        command = shlex.join(
            [
                sys.executable,
                "-c",
                "import json,sys; from pathlib import Path; "
                "payload=json.load(sys.stdin); "
                "log=Path('start-inputs.jsonl'); "
                "log.open('a').write(json.dumps(payload)+'\\n'); "
                f"print({output!r})",
            ]
        )
        identity = {
            "event_name": "session_start",
            "hooks": [{"type": "command", "command": command, "timeout": 600, "async": False}],
        }
        if source_kind == "clear":
            identity["matcher"] = "clear"
        fingerprint = (
            "sha256:"
            + sha256(
                json.dumps(
                    identity, sort_keys=True, ensure_ascii=False, separators=(",", ":")
                ).encode()
            ).hexdigest()
        )
        source = tmp_path / "config.toml"
        document = (
            "[[hooks.SessionStart]]\n"
            + ('matcher="clear"\n' if source_kind == "clear" else "")
            + '[[hooks.SessionStart.hooks]]\ntype="command"\n'
            f"command={json.dumps(command)}\n"
            f"[hooks.state.{json.dumps(f'{source}:session_start:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        settings = CorkiSettings(
            tmp_path,
            skills_enabled=False,
            plugins_enabled=False,
            configuration=LocalConfigState((ConfigLayer(source, "user", contents=document),)),
        )
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(())

            async def aclose(self):
                pass

        async def create(settings, thread=None, **kwargs):
            return await LangGraphRuntime.acreate(
                settings=settings,
                model=Model(),
                registry=ToolRegistry(),
                database_path=tmp_path / "state.db",
                home_path=tmp_path,
                thread_id=thread,
                **kwargs,
            )

        thread = new_thread_id() if source_kind == "explicit_new" else None
        if source_kind in {"resume", "resume_empty", "clear"}:
            warm = await create(replace(settings, configuration=LocalConfigState(())))
            try:
                if source_kind in {"resume", "clear"}:
                    assert isinstance([e async for e in warm.stream("SEED")][-1], TurnCompleted)
                else:
                    await warm._ensure_ready()
                    assert await warm._repository.load_items(warm.thread_id) == ()
                thread = warm.thread_id
                original_history = await warm._repository.load_items(thread)
            finally:
                await warm.aclose()
            requests.clear()
        if source_kind == "clear":
            original_thread, thread = thread, None
        runtime = await create(
            settings,
            thread,
            **({"session_start_source": "clear"} if source_kind == "clear" else {}),
        )
        try:
            if creation_fault is not None:
                original_create = runtime._repository.create_thread
                failure = OSError("thread creation boundary")

                async def fail_create(*args, **kwargs):
                    if creation_fault == "after_commit":
                        await original_create(*args, **kwargs)
                    raise failure

                monkeypatch.setattr(runtime._repository, "create_thread", fail_create)
                with pytest.raises(OSError) as caught:
                    await runtime._ensure_ready()
                assert caught.value is failure
                assert not requests and not (tmp_path / "start-inputs.jsonl").exists()
                assert not runtime._writer.held
                assert await runtime._repository.thread_exists(runtime.thread_id) == (
                    source_kind in {"resume", "resume_empty"} or creation_fault == "after_commit"
                )
                monkeypatch.setattr(runtime._repository, "create_thread", original_create)
            events = [e async for e in runtime.stream("FIRST")]
            log = tmp_path / "start-inputs.jsonl"
            assert log.exists(), "Trusted SessionStart hook was never executed"
            payloads = [json.loads(line) for line in log.read_text().splitlines()]
            assert len(payloads) == 1
            assert payloads[0]["hook_event_name"] == "SessionStart"
            assert payloads[0]["source"] == (
                "resume"
                if source_kind in {"resume", "resume_empty"}
                else "clear"
                if source_kind == "clear"
                else "startup"
            )
            assert payloads[0]["session_id"] == str(runtime.session_id)
            assert isinstance(events[-1], TurnCompleted)
            assert len(requests) == int(not stop)
            history = await runtime._repository.load_items(runtime.thread_id)
            assert any(
                isinstance(item, UserMessageItem) and item.content == "FIRST" for item in history
            ) == (not stop)
            assert any(getattr(item, "content", "") == "START_CONTEXT" for item in history)
            if not stop:
                items = requests[0].items
                context_index = next(
                    i
                    for i, item in enumerate(items)
                    if getattr(item, "content", "") == "START_CONTEXT"
                )
                user_index = next(
                    i
                    for i, item in enumerate(items)
                    if isinstance(item, UserMessageItem) and item.content == "FIRST"
                )
                assert context_index < user_index
            assert isinstance([e async for e in runtime.stream("SECOND")][-1], TurnCompleted)
            assert len(log.read_text().splitlines()) == 1
            assert len(requests) == 1 + int(not stop)
            if source_kind == "clear":
                assert runtime.thread_id != original_thread
                assert await runtime._repository.load_items(original_thread) == original_history
                assert not any(
                    isinstance(item, UserMessageItem) and item.content == "SEED"
                    for request in requests
                    for item in request.items
                )
        finally:
            await runtime.aclose()
        if source_kind == "clear":
            # Existing identity is never silently cleared or relabelled as a new start.
            invalid = await create(settings, original_thread, session_start_source="clear")
            try:
                with pytest.raises(ValueError, match="requires a new thread"):
                    await invalid._ensure_ready()
                assert not invalid._writer.held
                assert await invalid._repository.load_items(original_thread) == original_history
                assert len(log.read_text().splitlines()) == 1
            finally:
                await invalid.aclose()
            cold = await create(settings, runtime.thread_id)
            try:
                assert [e async for e in cold.resume_pending()] == []
                assert isinstance([e async for e in cold.stream("COLD")][-1], TurnCompleted)
                assert len(log.read_text().splitlines()) == 1
                assert any(
                    isinstance(item, UserMessageItem) and item.content == "SECOND"
                    for item in requests[-1].items
                )
                assert await cold._repository.load_items(original_thread) == original_history
            finally:
                await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("source_kind", ["child", "review", "compact"])
@pytest.mark.parametrize("explicit_id", [False, True])
def test_subagent_start_has_no_stop_control(tmp_path, monkeypatch, source_kind, explicit_id):
    from corki.core.stop_hooks import command_identity
    from corki.protocol.ids import ThreadId
    from corki.protocol.session_source import SessionSource, SubAgentSource, ThreadSpawnSource

    async def scenario():
        source = tmp_path / "config.toml"
        fingerprint, _ = command_identity(
            {"type": "command", "command": "inspect"},
            event_name="SubagentStart",
            matcher="reviewer",
        )
        document = (
            '[[hooks.SubagentStart]]\nmatcher="reviewer"\n[[hooks.SubagentStart.hooks]]\n'
            'type="command"\ncommand="inspect"\n'
            f"[hooks.state.{json.dumps(f'{source}:subagent_start:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        calls, requests = [], []

        async def runner(command, payload, **kwargs):
            calls.append(payload)
            return {
                "exit_code": 0,
                "stdout": json.dumps(
                    {
                        "continue": False,
                        "hookSpecificOutput": {
                            "hookEventName": "SubagentStart",
                            "additionalContext": "CHILD_CONTEXT",
                        },
                    }
                ),
            }

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(())

            async def aclose(self):
                pass

        provenance = SessionSource.subagent(
            SubAgentSource(
                "thread_spawn", ThreadSpawnSource(ThreadId("parent"), 1, agent_role="reviewer")
            )
            if source_kind == "child"
            else SubAgentSource(source_kind)
        )
        monkeypatch.setattr("corki.core.start_hooks.run_command", runner)
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                plugins_enabled=False,
                configuration=LocalConfigState((ConfigLayer(source, "user", contents=document),)),
            ),
            model=Model(),
            registry=ToolRegistry(),
            session_source=provenance,
            thread_id=new_thread_id() if explicit_id else None,
            database_path=tmp_path / "state.db",
            home_path=tmp_path,
        )
        try:
            events = [e async for e in runtime.stream("FIRST")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(requests) == 1
            assert len(calls) == int(source_kind == "child")
            assert ("CHILD_CONTEXT" in str(requests[0].items)) == (source_kind == "child")
            if calls:
                assert calls[0]["agent_type"] == "reviewer"
                assert calls[0]["agent_id"] == str(runtime.thread_id)
                assert calls[0]["turn_id"] == str(events[-1].turn_id)
                assert "source" not in calls[0]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
