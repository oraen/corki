"""User input inspection must happen before model sampling and history admission."""

import asyncio
import json
import shlex
import sys
from hashlib import sha256

import pytest

from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelTextDelta
from corki.protocol.events import AssistantTextDelta, TurnCancelled, TurnCompleted
from corki.protocol.items import UserMessageItem
from corki.tools import ToolRegistry


@pytest.mark.parametrize(
    "policy", ["stop", "context", "plain", "block", "invalid_block", "exit2", "allow"]
)
@pytest.mark.parametrize("steering", [False, True])
def test_user_prompt_submit_inspects_before_sampling(tmp_path, policy, steering):
    async def scenario():
        output = (
            {"continue": False, "stopReason": "PROMPT_REJECTED"}
            if policy == "stop"
            else {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": "PROMPT_CONTEXT",
                }
            }
        )
        if policy in {"block", "invalid_block"}:
            output = {"decision": "block", "reason": "reject" if policy == "block" else " "}
        elif policy == "allow":
            output = {}
        stdout = "PROMPT_CONTEXT" if policy == "plain" else json.dumps(output)
        command = shlex.join(
            [
                sys.executable,
                "-c",
                "import json,sys; from pathlib import Path; "
                "payload=json.load(sys.stdin); "
                "Path('prompt-payload.json').write_text(json.dumps(payload)); "
                "initial=payload['prompt']=='INITIAL'; "
                f"print('{{}}' if initial else {json.dumps(stdout)}); "
                + (
                    "print('reject',file=sys.stderr); sys.exit(0 if initial else 2)"
                    if policy == "exit2"
                    else ""
                ),
            ]
        )
        identity = {
            "event_name": "user_prompt_submit",
            "hooks": [
                {
                    "type": "command",
                    "command": command,
                    "timeout": 600,
                    "async": False,
                }
            ],
        }
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
            '[[hooks.UserPromptSubmit]]\n[[hooks.UserPromptSubmit.hooks]]\ntype="command"\n'
            f"command={json.dumps(command)}\n"
            f"[hooks.state.{json.dumps(f'{source}:user_prompt_submit:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                if steering and len(requests) == 1:
                    yield ModelTextDelta("steer now")
                yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                plugins_enabled=False,
                configuration=LocalConfigState((ConfigLayer(source, "user", contents=document),)),
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "state.db",
            home_path=tmp_path,
        )
        try:
            events = []
            async for e in runtime.stream(
                "INITIAL" if steering else "USER_PROMPT", realtime=steering
            ):
                events.append(e)
                if isinstance(e, AssistantTextDelta):
                    await runtime.steer("USER_PROMPT")
            marker = tmp_path / "prompt-payload.json"
            assert marker.exists(), "Trusted UserPromptSubmit hook was never executed"
            payload = json.loads(marker.read_text())
            assert payload["prompt"] == "USER_PROMPT"
            assert payload["hook_event_name"] == "UserPromptSubmit"
            assert payload["session_id"] == str(runtime.session_id)
            if policy in {"stop", "block", "exit2"}:
                assert len(requests) == int(steering)
                assert isinstance(events[-1], TurnCompleted)
                assert not any(
                    isinstance(item, UserMessageItem) and item.content == "USER_PROMPT"
                    for item in await runtime._repository.load_items(runtime.thread_id)
                )
            else:
                assert isinstance(events[-1], TurnCompleted)
                assert len(requests) == 1 + int(steering)
                assert ("PROMPT_CONTEXT" in str(requests[-1].items)) == (
                    policy in {"context", "plain"}
                )
                if policy in {"context", "plain"}:
                    items = requests[-1].items
                    user_index = next(
                        i
                        for i, item in enumerate(items)
                        if isinstance(item, UserMessageItem) and item.content == "USER_PROMPT"
                    )
                    context_index = next(
                        i
                        for i, item in enumerate(items)
                        if "PROMPT_CONTEXT" in getattr(item, "content", "")
                    )
                    assert user_index < context_index
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("reject", [False, True, "cancel"])
def test_terminal_pending_input_is_inspected(tmp_path, monkeypatch, reject):
    async def scenario():
        from corki.core.stop_hooks import command_identity

        source = tmp_path / "config.toml"
        document = ""
        for event, key in (("Stop", "stop"), ("UserPromptSubmit", "user_prompt_submit")):
            fingerprint, _ = command_identity(
                {"type": "command", "command": "inspect"}, event_name=event
            )
            document += (
                f'[[hooks.{event}]]\n[[hooks.{event}.hooks]]\ntype="command"\ncommand="inspect"\n'
                f"[hooks.state.{json.dumps(f'{source}:{key}:0:0')}]\ntrusted_hash={json.dumps(fingerprint)}\n"
            )
        prompts, requests = [], []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                plugins_enabled=False,
                configuration=LocalConfigState((ConfigLayer(source, "user", contents=document),)),
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "state.db",
            home_path=tmp_path,
        )

        async def runner(command, payload, **kwargs):
            if payload["hook_event_name"] == "Stop":
                await runtime.steer("LATE_INPUT")
                output = {"continue": False}
            else:
                prompts.append(payload["prompt"])
                if reject == "cancel" and payload["prompt"] == "LATE_INPUT":
                    raise asyncio.CancelledError
                output = (
                    (
                        {"continue": False}
                        if reject
                        else {
                            "hookSpecificOutput": {
                                "hookEventName": "UserPromptSubmit",
                                "additionalContext": "LATE_CONTEXT",
                            }
                        }
                    )
                    if payload["prompt"] == "LATE_INPUT"
                    else {}
                )
            return {"exit_code": 0, "stdout": json.dumps(output), "stderr": ""}

        monkeypatch.setattr("corki.core.stop_hooks.run_command", runner)
        monkeypatch.setattr("corki.core.prompt_hooks.run_command", runner)
        try:
            events = []
            async with asyncio.timeout(5):
                try:
                    async for event in runtime.stream("INITIAL", realtime=True):
                        events.append(event)
                except asyncio.CancelledError:
                    assert reject == "cancel"
            assert isinstance(events[-1], TurnCancelled if reject == "cancel" else TurnCompleted)
            assert prompts == ["INITIAL", "LATE_INPUT"]
            assert len(requests) == 1
            history = await runtime._repository.load_items(runtime.thread_id)
            contents = [getattr(item, "content", "") for item in history]
            assert ("LATE_INPUT" in contents) == (not reject)
            assert ("LATE_CONTEXT" in contents) == (not reject)
            if not reject:
                assert contents.index("LATE_INPUT") < contents.index("LATE_CONTEXT")
            assert not runtime._realtime.unrecorded_items
            if reject == "cancel":
                assert [item.content for item in events[-1].unsubmitted_inputs] == ["LATE_INPUT"]
                assert [item.content for item in runtime.take_unsubmitted_inputs()] == [
                    "LATE_INPUT"
                ]
                assert await runtime._repository.latest_running_turn(runtime.thread_id) is None
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
