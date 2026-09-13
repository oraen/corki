"""Interrupt real graph commits, then resume hook outcomes from a cold Runtime."""

import asyncio
import json
import shlex
import sys
from dataclasses import replace

import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.core.stop_hooks import command_identity
from corki.models import ModelCompleted
from corki.protocol.events import HookCompleted, HookStarted, TurnCompleted, TurnFailed
from corki.protocol.ids import SessionId, ThreadId, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.session_source import (
    DEFAULT_SESSION_SOURCE,
    SessionSource,
    SubAgentSource,
    ThreadSpawnSource,
)
from corki.sessions import TurnRecord, TurnStatus


@pytest.mark.parametrize("window", ["unknown", "completed", "feedback"])
@pytest.mark.parametrize("event_name", ["Stop", "SubagentStop"])
@pytest.mark.parametrize(
    (
        "plugin_source",
        "migrate_key",
        "relocate_plugin",
        "legacy_payload",
        "transcript_failure",
        "kind",
    ),
    [
        (False, False, False, False, None, "command"),
        (False, True, False, False, None, "command"),
        (True, False, False, False, None, "command"),
        (True, False, True, False, None, "command"),
        (False, False, False, True, None, "command"),
        (False, False, False, 2, None, "command"),
        (False, False, False, False, "warm", "command"),
        (False, False, False, False, "cold", "command"),
        (False, False, False, False, None, "mcp_tool"),
        (False, True, False, False, None, "mcp_tool"),
    ],
)
def test_stop_hook_cold_recovery_never_replays_unknown_or_completed_command(
    tmp_path,
    monkeypatch,
    window,
    migrate_key,
    plugin_source,
    relocate_plugin,
    legacy_payload,
    event_name,
    transcript_failure,
    kind,
):
    event_key = "stop" if event_name == "Stop" else "subagent_stop"
    session_source = (
        DEFAULT_SESSION_SOURCE
        if event_name == "Stop"
        else SessionSource.subagent(
            SubAgentSource(
                "thread_spawn", ThreadSpawnSource(ThreadId("parent"), 1, agent_role="reviewer")
            )
        )
    )

    async def scenario():
        command = shlex.join(
            [
                sys.executable,
                "-c",
                "import json,sys; from pathlib import Path; p=json.load(sys.stdin); "
                "Path('hook-calls').open('a').write(str(p['stop_hook_active'])+'\\n'); "
                "print(json.dumps({} if p['stop_hook_active'] else "
                "{'decision':'block','reason':'CHECK_ONCE'}))",
            ]
        )
        handler = {"type": "command", "command": command}
        if kind == "mcp_tool":
            handler = {
                "type": kind,
                "server": "policy",
                "tool": "review",
                "input": {"active": "${stop_hook_active}"},
            }
        fingerprint, _ = command_identity(handler, event_name=event_name)
        source = tmp_path / "config.toml"
        key = f"file:{source}:{event_key}:0:0"
        document = (
            f"[[hooks.{event_name}]]\n[[hooks.{event_name}.hooks]]\ntype='command'\ncommand="
            + json.dumps(command)
            + f"\n[hooks.state.{json.dumps(key)}]\ntrusted_hash={json.dumps(fingerprint)}\n"
        )
        calls = []
        if kind == "mcp_tool":
            document = (
                f"[[hooks.{event_name}]]\n[[hooks.{event_name}.hooks]]\n"
                'type="mcp_tool"\nserver="policy"\ntool="review"\n'
                f'[hooks.{event_name}.hooks.input]\nactive="${{stop_hook_active}}"\n'
                f"[hooks.state.{json.dumps(key)}]\ntrusted_hash={json.dumps(fingerprint)}\n"
            )

            class Client:
                is_closed = False
                server_instructions = None

                def __init__(self, settings):
                    self.settings = settings

                async def start(self):
                    pass

                async def list_tools(self):
                    return ({"name": "review", "inputSchema": {"type": "object"}},)

                async def request(self, method, params):
                    assert method == "tools/call"
                    active = params["arguments"]["active"]
                    assert type(active) is bool
                    calls.append(str(active))
                    result = {} if active else {"decision": "block", "reason": "CHECK_ONCE"}
                    return {"content": [{"type": "text", "text": json.dumps(result)}]}

                async def aclose(self):
                    self.is_closed = True

            monkeypatch.setattr("corki.mcp.manager.create_client", Client)

        def hook_calls():
            return (
                calls if kind == "mcp_tool" else (tmp_path / "hook-calls").read_text().splitlines()
            )

        plugin_roots = ()
        if plugin_source:
            root = tmp_path / "bundle"
            (root / ".codex-plugin").mkdir(parents=True)
            (root / "hooks").mkdir()
            (root / ".codex-plugin/plugin.json").write_text('{"name":"bundle"}')
            (root / "hooks/hooks.json").write_text(
                json.dumps(
                    {"hooks": {event_name: [{"hooks": [{"type": "command", "command": command}]}]}}
                )
            )
            key = f"bundle:hooks/hooks.json:{event_key}:0:0"
            document = f"[hooks.state.{json.dumps(key)}]\ntrusted_hash={json.dumps(fingerprint)}\n"
            plugin_roots = (root,)
        settings = CorkiSettings(
            tmp_path,
            skills_enabled=False,
            plugins_enabled=plugin_source,
            plugin_dirs=plugin_roots,
            configuration=LocalConfigState((ConfigLayer(source, "user", contents=document),)),
            mcp_servers=(MCPServerSettings("policy", "http", url="https://fixture.invalid"),)
            if kind == "mcp_tool"
            else (),
        )
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                assert len(requests) <= 2
                if len(requests) == 2:
                    assert (
                        sum(
                            isinstance(i, ContextItem) and i.content == "CHECK_ONCE"
                            for i in request.items
                        )
                        == 1
                    )
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class Sink:
            async def emit(self, event):
                pass

        database = tmp_path / "session.db"
        warm = await LangGraphRuntime.acreate(
            settings=settings,
            model=Model(),
            database_path=database,
            home_path=tmp_path / "home",
            session_source=session_source,
            session_id=SessionId("legacy-explicit-session") if legacy_payload else None,
        )
        try:
            await warm._ensure_ready()
            if kind == "mcp_tool":
                await warm._mcp_manager.start()
            thread, turn = warm.thread_id, new_turn_id()
            user = UserMessageItem("finish after checking", turn)
            repository = warm._repository
            await repository.save_turn(TurnRecord(turn, thread, TurnStatus.RUNNING, user.content))
            await repository.append_items(thread, (user,))
            entered = asyncio.Event()
            complete = type(repository).complete_hook_execution
            append = type(repository).append_items

            async def held_complete(self, *args):
                if window != "unknown":
                    await complete(self, *args)
                if window in ("unknown", "completed"):
                    entered.set()
                    await asyncio.Event().wait()

            async def held_feedback(self, thread_id, items):
                await append(self, thread_id, items)
                if window == "feedback" and any(
                    isinstance(i, ContextItem) and i.content_kind == f"hook.{event_key}.feedback"
                    for i in items
                ):
                    entered.set()
                    await asyncio.Event().wait()

            with monkeypatch.context() as patch:

                async def unavailable_transcript(thread_id):
                    raise OSError("injected transcript publication failure")

                if transcript_failure == "warm":
                    patch.setattr(repository, "materialize_transcript", unavailable_transcript)
                if legacy_payload:
                    save_batch = repository.save_hook_batch

                    async def save_legacy_batch(thread_id, turn_id, key, snapshot):
                        if not key.startswith(f"{event_key}:"):
                            await save_batch(thread_id, turn_id, key, snapshot)
                            return
                        # Emit the old writer's exact payload before the command
                        # executes, not by rewriting an already claimed effect.
                        snapshot["version"] = int(legacy_payload)
                        snapshot["payload"]["transcript_path"] = None
                        if event_name == "SubagentStop":
                            snapshot["payload"]["agent_transcript_path"] = None
                        if event_name == "Stop" and legacy_payload == 1:
                            snapshot["payload"]["session_id"] = str(thread_id)
                            snapshot["payload"].pop("permission_mode")
                            snapshot["payload"].pop("transcript_path")
                        await save_batch(thread_id, turn_id, key, snapshot)

                    patch.setattr(repository, "save_hook_batch", save_legacy_batch)
                patch.setattr(type(repository), "complete_hook_execution", held_complete)
                patch.setattr(type(repository), "append_items", held_feedback)
                task = asyncio.create_task(
                    warm._compiled.ainvoke(
                        _initial_state(thread, turn, settings, user),
                        context=GraphRunContext(events=Sink(), session_source=session_source),
                        config=warm._graph_config(turn),
                        durability="sync",
                    )
                )
                try:
                    await asyncio.wait_for(entered.wait(), 5)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            original = await repository.load_items(thread)
            with repository._connect() as connection:
                execution_keys = connection.execute(
                    "SELECT execution_key, request_json FROM hook_executions"
                ).fetchall()
            assert len(execution_keys) == 1
            assert ":file:" + key.removeprefix("file:") in execution_keys[0][0]
            if legacy_payload:
                assert warm.session_id != thread
                legacy_input = json.loads(execution_keys[0][1])["payload"]
                assert legacy_input["session_id"] == (
                    str(thread)
                    if event_name == "Stop" and legacy_payload == 1
                    else "legacy-explicit-session"
                )
                assert ("permission_mode" in legacy_input) == (
                    event_name == "SubagentStop" or legacy_payload == 2
                )
            if plugin_source:
                assert json.loads(execution_keys[0][1])["environment"]["PLUGIN_ROOT"] == str(root)
            assert len(requests) == 1
            assert hook_calls() == ["False"]
            checkpoint = await warm._checkpointer.aget_tuple(warm._graph_config(turn))
            assert checkpoint is not None
        finally:
            await warm.aclose()

        if migrate_key:
            settings = replace(
                settings,
                configuration=LocalConfigState(
                    (
                        ConfigLayer(
                            source,
                            "user",
                            contents=document.replace(
                                json.dumps(key), json.dumps(key.removeprefix("file:"))
                            ),
                        ),
                    )
                ),
            )
        if relocate_plugin:
            moved = tmp_path / "moved-bundle"
            root.rename(moved)
            settings = replace(settings, plugin_dirs=(moved,))
        cold = await LangGraphRuntime.acreate(
            settings=settings,
            model=Model(),
            database_path=database,
            thread_id=thread,
            home_path=tmp_path / "home",
            session_source=session_source,
        )
        try:
            if kind == "mcp_tool":
                await cold._mcp_manager.start()
            if transcript_failure == "cold":
                monkeypatch.setattr(
                    cold._repository, "materialize_transcript", unavailable_transcript
                )
            events = [e async for e in cold.resume_pending()]
            with cold._repository._connect() as connection:
                retained_request = connection.execute(
                    "SELECT request_json FROM hook_executions WHERE execution_key=?",
                    (execution_keys[0][0],),
                ).fetchone()
            assert retained_request[0] == execution_keys[0][1]
            if legacy_payload:
                assert cold.session_id == "legacy-explicit-session"
                with cold._repository._connect() as connection:
                    retained = connection.execute(
                        "SELECT request_json FROM hook_executions WHERE execution_key=?",
                        (execution_keys[0][0],),
                    ).fetchone()
                assert retained[0] == execution_keys[0][1]
            if window == "unknown" or relocate_plugin:
                assert isinstance(events[-1], TurnFailed), events[-1]
                assert ("identity collision" if relocate_plugin else "unknown") in events[-1].error
                assert len(requests) == 1
                assert hook_calls() == ["False"]
            else:
                assert isinstance(events[-1], TurnCompleted), events[-1]
                assert len(requests) == 2
                assert hook_calls() == ["False", "True"]
            stored = await cold._repository.load_items(thread)
            assert stored[: len(original)] == original
            assert sum(isinstance(i, UserMessageItem) for i in stored) == 1
            assert not any(isinstance(i, ToolResultItem) for i in stored)
            assert sum(
                isinstance(i, ContextItem) and i.content_kind == f"hook.{event_key}.feedback"
                for i in stored
            ) == int(window == "feedback" or (window == "completed" and not relocate_plugin))
            assert [e async for e in cold.resume_pending()] == []
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("event_name", ["Stop", "SubagentStop"])
@pytest.mark.parametrize("unknown_index", [None, 0, 1])
@pytest.mark.parametrize("pending_first", [False, True])
def test_concurrent_batch_cold_recovery_validates_all_results_before_replay(
    tmp_path, monkeypatch, event_name, unknown_index, pending_first
):
    async def scenario():
        missing_index = None if unknown_index is None else unknown_index + int(pending_first)
        count = 3 if pending_first else 2
        event_key = "stop" if event_name == "Stop" else "subagent_stop"
        source = (
            DEFAULT_SESSION_SOURCE
            if event_name == "Stop"
            else SessionSource.subagent(
                SubAgentSource("thread_spawn", ThreadSpawnSource(ThreadId("parent"), 1))
            )
        )
        config_file = tmp_path / "config.toml"
        definition = f"[[hooks.{event_name}]]\n"
        approvals = ""
        for index in range(count):
            command = shlex.join(
                [
                    sys.executable,
                    "-c",
                    "import json,sys; from pathlib import Path; json.load(sys.stdin); "
                    f"Path('effect-{index}').open('a').write('executed\\n'); print('{{}}')",
                ]
            )
            fingerprint, _ = command_identity(
                {"type": "command", "command": command}, event_name=event_name
            )
            definition += (
                f"[[hooks.{event_name}.hooks]]\ntype='command'\ncommand={json.dumps(command)}\n"
            )
            key = f"{config_file}:{event_key}:0:{index}"
            approvals += (
                f"[hooks.state.{json.dumps(key)}]\ntrusted_hash={json.dumps(fingerprint)}\n"
            )
        settings = CorkiSettings(
            tmp_path,
            skills_enabled=False,
            plugins_enabled=False,
            configuration=LocalConfigState(
                (ConfigLayer(config_file, "user", contents=definition + approvals),)
            ),
        )
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                assert len(requests) == 1, "Recovery must not sample again"
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class Sink:
            async def emit(self, event):
                pass

        database = tmp_path / "session.db"
        warm = await LangGraphRuntime.acreate(
            settings=settings,
            model=Model(),
            database_path=database,
            home_path=tmp_path / "home",
            session_source=source,
        )
        try:
            await warm._ensure_ready()
            thread, turn = warm.thread_id, new_turn_id()
            user = UserMessageItem("finish once", turn)
            repository = warm._repository
            await repository.save_turn(TurnRecord(turn, thread, TurnStatus.RUNNING, user.content))
            await repository.append_items(thread, (user,))
            complete = repository.complete_hook_execution
            claim = repository.claim_hook_execution
            arrived = set()
            both = asyncio.Event()
            pending = asyncio.Event()

            async def held_claim(thread_id, turn_id, key, request):
                if pending_first and key.endswith(":0"):
                    pending.set()
                    await asyncio.Future()
                return await claim(thread_id, turn_id, key, request)

            async def held_complete(thread_id, turn_id, key, request, result):
                index = int(key.rsplit(":", 1)[1])
                if index != missing_index:
                    await complete(thread_id, turn_id, key, request, result)
                arrived.add(index)
                if len(arrived) == 2:
                    both.set()
                await asyncio.Future()

            with monkeypatch.context() as patch:
                patch.setattr(repository, "complete_hook_execution", held_complete)
                patch.setattr(repository, "claim_hook_execution", held_claim)
                task = asyncio.create_task(
                    warm._compiled.ainvoke(
                        _initial_state(thread, turn, settings, user),
                        context=GraphRunContext(events=Sink(), session_source=source),
                        config=warm._graph_config(turn),
                        durability="sync",
                    )
                )
                try:
                    await asyncio.wait_for(both.wait(), 5)
                    if pending_first:
                        await asyncio.wait_for(pending.wait(), 5)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            original = await repository.load_items(thread)
            with repository._connect() as connection:
                rows = connection.execute(
                    "SELECT execution_key, result_json FROM hook_executions ORDER BY execution_key"
                ).fetchall()
            assert len(rows) == 2
            assert [row[1] is None for row in rows] == [unknown_index == 0, unknown_index == 1]
            if pending_first:
                assert not (tmp_path / "effect-0").exists()
            assert await warm._checkpointer.aget_tuple(warm._graph_config(turn)) is not None
        finally:
            await warm.aclose()

        cold = await LangGraphRuntime.acreate(
            settings=settings,
            model=Model(),
            database_path=database,
            thread_id=thread,
            home_path=tmp_path / "home",
            session_source=source,
        )
        try:
            events = [event async for event in cold.resume_pending()]
            if unknown_index is None:
                assert isinstance(events[-1], TurnCompleted)
            else:
                assert isinstance(events[-1], TurnFailed)
                assert "unknown" in events[-1].error
                assert not any(isinstance(event, (HookStarted, HookCompleted)) for event in events)
            with cold._repository._connect() as connection:
                recovered = connection.execute(
                    "SELECT execution_key, result_json FROM hook_executions ORDER BY execution_key"
                ).fetchall()
            if unknown_index is not None:
                assert [tuple(row) for row in recovered] == [tuple(row) for row in rows]
            else:
                assert len(recovered) == count
                assert all(row[1] is not None for row in recovered)
            assert len(requests) == 1
            for index in range(count):
                if pending_first and index == 0 and unknown_index is not None:
                    assert not (tmp_path / "effect-0").exists()
                    continue
                assert (tmp_path / f"effect-{index}").read_text().splitlines() == ["executed"]
            stored = await cold._repository.load_items(thread)
            assert stored == original
            assert sum(isinstance(item, UserMessageItem) for item in stored) == 1
            assert [event async for event in cold.resume_pending()] == []
        finally:
            await cold.aclose()

    asyncio.run(scenario())
