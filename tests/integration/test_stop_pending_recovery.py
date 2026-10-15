"""Pending input cannot bypass an already claimed Stop during cold recovery."""

import asyncio
import json
import shlex
import sqlite3
import sys
from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.core.stop_hooks import command_identity
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted, TurnFailed
from corki.protocol.ids import new_turn_id
from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id
from corki.sessions import TurnRecord, TurnStatus


@pytest.mark.parametrize("committed", [None, False, True])
@pytest.mark.parametrize("late_input", [False, True])
@pytest.mark.parametrize("change", ["same", "removed", "revoked", "added", "changed"])
@pytest.mark.parametrize("handlers", [1, 2])
def test_pending_input_does_not_bypass_prior_hook_claim(
    tmp_path, monkeypatch, committed, late_input, change, handlers, corruption=None
):
    async def scenario():
        command = shlex.join(
            [
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; Path('calls').open('a').write('once\\n'); "
                    "print('{\"continue\": false}')"
                ),
            ]
        )
        fingerprint = command_identity({"type": "command", "command": command})[0]
        source = tmp_path / "config.toml"
        document = (
            '[[hooks.Stop]]\n[[hooks.Stop.hooks]]\ntype="command"\ncommand='
            + json.dumps(command)
            + "\n[hooks.state."
            + json.dumps(f"{source}:stop:0:0")
            + "]\ntrusted_hash="
            + json.dumps(fingerprint)
            + "\n"
        )
        if handlers == 2:
            second_command = command.replace("once", "second") if change == "reordered" else command
            second_fingerprint = command_identity({"type": "command", "command": second_command})[0]
            declaration, approval = document.split("[hooks.state.", 1)
            document = (
                declaration
                + '[[hooks.Stop.hooks]]\ntype="command"\ncommand='
                + json.dumps(second_command)
                + "\n[hooks.state."
                + approval
                + "[hooks.state."
                + json.dumps(f"{source}:stop:0:1")
                + "]\ntrusted_hash="
                + json.dumps(second_fingerprint)
                + "\n"
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
            settings=settings, model=Model(), database_path=database
        )
        try:
            await warm._ensure_ready()
            thread, turn = warm.thread_id, new_turn_id()
            user = UserMessageItem("initial", turn)
            await warm._repository.save_turn(
                TurnRecord(turn, thread, TurnStatus.RUNNING, user.content)
            )
            await warm._repository.append_items(thread, (user,))
            entered = asyncio.Event()
            claim = type(warm._repository).claim_hook_execution
            complete = type(warm._repository).complete_hook_execution
            save_batch = type(warm._repository).save_hook_batch

            async def held_claim(self, thread_id, turn_id, key, request):
                # Keep this partial-batch recovery window deterministic now
                # that synchronous handlers start concurrently. Only the first
                # handler may claim/execute before the simulated interruption.
                if handlers == 2 and key.endswith(":1"):
                    await asyncio.Future()
                return await claim(self, thread_id, turn_id, key, request)

            async def held_batch(self, *args):
                await save_batch(self, *args)
                if committed is None and args[2].startswith("stop:"):
                    if late_input:
                        await self.append_items(thread, (UserMessageItem("late", turn),))
                    entered.set()
                    await asyncio.Future()

            async def held(self, *args):
                if committed:
                    await complete(self, *args)
                if late_input:
                    await self.append_items(thread, (UserMessageItem("late", turn),))
                entered.set()
                await asyncio.Future()

            with monkeypatch.context() as patch:
                patch.setattr(type(warm._repository), "claim_hook_execution", held_claim)
                patch.setattr(type(warm._repository), "save_hook_batch", held_batch)
                patch.setattr(type(warm._repository), "complete_hook_execution", held)
                task = asyncio.create_task(
                    warm._compiled.ainvoke(
                        _initial_state(thread, turn, settings, user),
                        context=GraphRunContext(events=Sink()),
                        config=warm._graph_config(turn),
                        durability="sync",
                    )
                )
                try:
                    await asyncio.wait_for(entered.wait(), 5)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            assert await warm._checkpointer.aget_tuple(warm._graph_config(turn)) is not None
        finally:
            await warm.aclose()
        if corruption is not None:
            # The warm owner has closed. Corrupt only this fixture's durable rows,
            # then exercise the real cold repository decoder and recovery path.
            with sqlite3.connect(database) as connection:
                if corruption == "version":
                    key, encoded = connection.execute(
                        "SELECT batch_key, snapshot_json FROM hook_batches "
                        "WHERE turn_id=? AND batch_key LIKE 'stop:%'",
                        (str(turn),),
                    ).fetchone()
                    snapshot = json.loads(encoded)
                    snapshot["version"] = 999
                    connection.execute(
                        "UPDATE hook_batches SET snapshot_json=? WHERE batch_key=?",
                        (json.dumps(snapshot), key),
                    )
                elif corruption == "request":
                    connection.execute(
                        "UPDATE hook_executions SET request_json='{}' WHERE turn_id=?",
                        (str(turn),),
                    )
                elif corruption == "execution_key":
                    connection.execute(
                        "UPDATE hook_executions SET execution_key=execution_key || ':unexpected' "
                        "WHERE turn_id=?",
                        (str(turn),),
                    )
                else:
                    raise AssertionError(corruption)
        if change in {"removed", "revoked"}:
            layers = (
                ()
                if change == "removed"
                else tuple(
                    replace(
                        layer,
                        contents=layer.contents.replace(
                            "trusted_hash=", "enabled=false\ntrusted_hash="
                        ),
                    )
                    for layer in settings.configuration.layers
                )
            )
            settings = replace(settings, configuration=LocalConfigState(layers))
        elif change == "reordered":
            changed_document = document.replace(json.dumps(command), "COMMAND_SWAP_PLACEHOLDER")
            changed_document = changed_document.replace(
                json.dumps(second_command), json.dumps(command)
            )
            changed_document = changed_document.replace(
                "COMMAND_SWAP_PLACEHOLDER", json.dumps(second_command)
            )
            changed_document = changed_document.replace(fingerprint, "HASH_SWAP_PLACEHOLDER")
            changed_document = changed_document.replace(second_fingerprint, fingerprint)
            changed_document = changed_document.replace("HASH_SWAP_PLACEHOLDER", second_fingerprint)
            settings = replace(
                settings,
                configuration=LocalConfigState(
                    (ConfigLayer(source, "user", contents=changed_document),)
                ),
            )
        elif change in {"added", "changed"}:
            new_command = command + " # " + change
            new_hash = command_identity({"type": "command", "command": new_command})[0]
            if change == "changed":
                changed_document = document.replace(json.dumps(command), json.dumps(new_command))
                changed_document = changed_document.replace(fingerprint, new_hash)
            else:
                declaration, approval = document.split("[hooks.state.", 1)
                changed_document = (
                    declaration
                    + '[[hooks.Stop.hooks]]\ntype="command"\ncommand='
                    + json.dumps(new_command)
                    + "\n[hooks.state."
                    + approval
                    + "[hooks.state."
                    + json.dumps(f"{source}:stop:0:{handlers}")
                    + "]\ntrusted_hash="
                    + json.dumps(new_hash)
                    + "\n"
                )
            settings = replace(
                settings,
                configuration=LocalConfigState(
                    (ConfigLayer(source, "user", contents=changed_document),)
                ),
            )
        cold = await LangGraphRuntime.acreate(
            settings=settings, model=Model(), database_path=database, thread_id=thread
        )
        try:
            events = [event async for event in cold.resume_pending()]
            completed = (
                corruption is None
                and change != "changed"
                and committed is not False
                and (change in {"same", "added"} or (committed is True and handlers == 1))
            )
            assert isinstance(events[-1], TurnCompleted if completed else TurnFailed), events[-1]
            if corruption is not None:
                expected = {
                    "version": "Unsupported hook batch snapshot version",
                    "request": "identity collision",
                    "execution_key": "unexpected execution identity",
                }[corruption]
                assert expected in events[-1].error
            elif change in {"changed", "reordered"}:
                assert "identity collision" in events[-1].error
            elif committed is False:
                assert "unknown" in events[-1].error
            elif not completed:
                assert "authorization was removed" in events[-1].error
            assert len(requests) == 1
            calls = tmp_path / "calls"
            actual = calls.read_text().splitlines() if calls.exists() else []
            assert actual == ["once"] * (
                handlers
                if completed and change in {"same", "added"}
                else int(committed is not None)
            )
            items = await cold._repository.load_items(thread)
            assert [i.content for i in items if isinstance(i, UserMessageItem)] == (
                ["initial", "late"] if late_input else ["initial"]
            )
            assert [event async for event in cold.resume_pending()] == []
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("committed", [None, False, True])
@pytest.mark.parametrize("late_input", [False, True])
def test_reordered_distinct_commands_cannot_replace_pending_hook_identities(
    tmp_path, monkeypatch, committed, late_input
):
    test_pending_input_does_not_bypass_prior_hook_claim(
        tmp_path, monkeypatch, committed, late_input, "reordered", 2
    )


@pytest.mark.parametrize("corruption", ["version", "request", "execution_key"])
@pytest.mark.parametrize("late_input", [False, True])
def test_corrupt_durable_hook_batch_cannot_admit_pending_sibling(
    tmp_path, monkeypatch, corruption, late_input
):
    test_pending_input_does_not_bypass_prior_hook_claim(
        tmp_path, monkeypatch, True, late_input, "same", 2, corruption=corruption
    )
