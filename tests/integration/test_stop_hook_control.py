"""Stop wins over all continuation requests regardless of declaration order."""

import asyncio
import json
import shlex
import sys

import pytest

import corki.core.stop_hooks as stop_hooks
from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.core.stop_hooks import command_identity
from corki.models import ModelCompleted
from corki.protocol.events import HookCompleted, HookStarted, TurnCompleted
from corki.protocol.items import AssistantMessageItem, ContextItem, UserMessageItem, new_step_id


@pytest.mark.parametrize("decisions", [("block", "stop"), ("stop", "block"), ("block", "block")])
@pytest.mark.parametrize("late_input", [False, True])
@pytest.mark.parametrize("limited", [False, True])
def test_stop_outcomes_are_aggregated_before_feedback_is_committed(
    tmp_path, monkeypatch, decisions, late_input, limited
):
    async def scenario():
        source = tmp_path / "config.toml"
        definition = "[[hooks.Stop]]\n"
        approvals = ""
        for index, decision in enumerate(decisions):
            output = (
                {"continue": False, "stopReason": "HALT"}
                if decision == "stop"
                else {
                    "decision": "block",
                    "reason": f"CHECK_{index}",
                }
            )
            output.update(systemMessage=f"DIAGNOSTIC_{index}", suppressOutput=True)
            command = shlex.join(
                [
                    sys.executable,
                    "-c",
                    "import json,sys; from pathlib import Path; p=json.load(sys.stdin); "
                    f"Path('hook-calls').open('a').write('{index}\\n'); "
                    f"print(json.dumps({{}} if p['stop_hook_active'] else {output!r}))",
                ]
            )
            definition += f"[[hooks.Stop.hooks]]\ntype='command'\ncommand={json.dumps(command)}\n"
            fingerprint = command_identity({"type": "command", "command": command})[0]
            approvals += (
                f"[hooks.state.{json.dumps(f'{source}:stop:0:{index}')} ]\n"
                f"trusted_hash={json.dumps(fingerprint)}\n"
            )
        requests = []
        blocked = "stop" not in decisions

        class Model:
            async def stream(self, request):
                requests.append(request)
                assert len(requests) <= (2 if blocked else 1)
                if len(requests) == 2:
                    assert [
                        i.content
                        for i in request.items
                        if isinstance(i, ContextItem) and i.content_kind == "hook.stop.feedback"
                    ] == ["CHECK_0", "CHECK_1"]
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
                max_steps=(2 if blocked else 1) if limited else None,
                configuration=LocalConfigState(
                    (ConfigLayer(source, "user", contents=definition + approvals),)
                ),
            ),
            model=Model(),
            database_path=tmp_path / "session.db",
            home_path=tmp_path / "home",
        )
        original = stop_hooks.run_command
        injected = False

        async def run_command(*args, **kwargs):
            nonlocal injected
            result = await original(*args, **kwargs)
            if late_input and not injected:
                injected = True
                await runtime.steer("arrived during hook")
            return result

        monkeypatch.setattr(stop_hooks, "run_command", run_command)
        try:
            events = [event async for event in runtime.stream("finish", realtime=True)]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            starts = [e for e in events if isinstance(e, HookStarted)]
            completed = [e for e in events if isinstance(e, HookCompleted)]
            assert [e.run.id for e in starts] == [e.run.id for e in completed]
            assert len(starts) == (4 if blocked else 2)
            assert [e.run.status for e in completed[:2]] == [
                "stopped" if decision == "stop" else "blocked" for decision in decisions
            ]
            assert [
                entry.text for e in completed for entry in e.run.entries if entry.kind == "warning"
            ] == ["DIAGNOSTIC_0", "DIAGNOSTIC_1"]
            assert len(requests) == (2 if blocked else 1)
            calls = (tmp_path / "hook-calls").read_text().splitlines()
            assert len(calls) == (4 if blocked else 2)
            # Effects can finish in either order; each complete batch still
            # precedes the next model step. Public results above remain ordered.
            assert all(
                sorted(calls[offset : offset + 2]) == ["0", "1"]
                for offset in range(0, len(calls), 2)
            )
            history = await runtime._repository.load_items(runtime.thread_id)
            assert [i.content for i in history if isinstance(i, UserMessageItem)] == (
                ["finish", "arrived during hook"] if late_input else ["finish"]
            )
            assert not runtime._realtime.active
            assert runtime.take_unsubmitted_inputs() == ()
            assert sum(
                isinstance(i, ContextItem) and i.content_kind == "hook.stop.feedback"
                for i in history
            ) == (2 if blocked else 0)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
