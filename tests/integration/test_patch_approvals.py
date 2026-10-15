"""Patch consent through real native preparation, Runtime, host transport and CLI."""

import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from test_bundled_execution import compiler as compiler
from test_execution_approval_cancel import observe
from test_filesystem_helper_runtime import workspace_policy

from corki.cli.application import CorkiApplication
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.execution.backend import file_operation
from corki.models import ModelCompleted
from corki.protocol.events import TurnCancelled, TurnCompleted, TurnFailed
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall


def patch(body):
    return f"*** Begin Patch\n{body}\n*** End Patch"


class Model:
    def __init__(self, mode):
        self.mode = mode
        self.patch = patch("*** Add File: allowed.txt\n+one")
        self.requests, self.turns = [], set()

    async def stream(self, request):
        self.requests.append(request)
        turn = request.items[-1].turn_id
        if turn in self.turns:
            yield ModelCompleted(())
            return
        self.turns.add(turn)
        arguments = {"patch": self.patch}
        call = (
            ToolCall(new_tool_call_id(), "apply_patch", arguments)
            if self.mode == "direct"
            else ToolCall(
                new_tool_call_id(),
                "exec",
                None,
                input_kind="freeform",
                raw_arguments="try { text(await tools.apply_patch("
                + json.dumps(arguments)
                + ")); } catch(e) { text(String(e)); }",
            )
        )
        yield ModelCompleted((ToolCallItem(call, turn, new_step_id()),))

    async def aclose(self):
        pass


async def create_runtime(root, compiler, model, *, policy=None):
    policy = policy or replace(workspace_policy(compiler, root), approval_policy_json='"untrusted"')
    return await LangGraphRuntime.acreate(
        settings=CorkiSettings(
            root, skills_enabled=False, tool_mode=model.mode, execution_permissions=policy
        ),
        model=model,
        home_path=root / "host",
        database_path=root / "state.db",
    )


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("host", ["api", "cli"])
@pytest.mark.parametrize("action", ["accept", "decline", "cancel"])
def test_patch_review_precedes_writes_and_has_one_terminal(tmp_path, compiler, mode, host, action):
    async def scenario():
        model = Model(mode)
        runtime = await create_runtime(tmp_path, compiler, model)
        prompts = []
        target = tmp_path / "allowed.txt"

        def check(request):
            prompts.append(request)
            assert request.kind == "patch_approval"
            assert not target.exists()
            params = request.params["_meta"]["tool_params"]
            assert params["patch"] == model.patch and params["files"] == [str(target)]
            assert params["changes"][0]["change"] == {"kind": "add", "content": "one\n"}

        async def respond(request):
            check(request)
            runtime.respond_execution_approval(request.request_id, action)

        class UI:
            async def read_elicitation(self, request):
                check(request)
                return action, {"scope": "once"}

        if host == "api":
            runtime.set_execution_approval_handler(respond)
        else:
            CorkiApplication(
                CorkiSettings(tmp_path), CorkiPaths.from_home(tmp_path / "host"), runtime, UI()
            )
        try:
            events = await asyncio.wait_for(observe(runtime, "apply reviewed patch"), 10)
            terminal = [
                e for e in events if isinstance(e, (TurnCompleted, TurnCancelled, TurnFailed))
            ]
            assert len(terminal) == 1 and isinstance(
                terminal[0], TurnCancelled if action == "cancel" else TurnCompleted
            )
            assert len(prompts) == 1 and target.exists() is (action == "accept")
            assert not runtime._process_manager.approvals.router._pending
            if action != "cancel":
                result = [i for i in model.requests[-1].items if isinstance(i, ToolResultItem)][-1]
                assert (
                    "Success. Updated" if action == "accept" else "patch approval rejected"
                ) in result.content
            assert len(model.requests) == (1 if action == "cancel" else 2)
            with pytest.raises(ValueError, match="Unknown"):
                runtime.respond_execution_approval(prompts[0].request_id, "cancel")
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("remember", [False, True])
def test_session_patch_consent_covers_subsets_but_not_new_move_destinations(
    tmp_path, compiler, mode, remember
):
    async def scenario():
        model = Model(mode)
        runtime = await create_runtime(tmp_path, compiler, model)
        prompts = []

        async def accept(request):
            prompts.append(request.params["_meta"]["tool_params"])
            runtime.respond_execution_approval(request.request_id, "accept", remember=remember)

        runtime.set_execution_approval_handler(accept)
        try:
            for body in (
                "*** Add File: a.txt\n+one\n*** Add File: b.txt\n+two",
                "*** Add File: a.txt\n+one",
                "*** Update File: a.txt\n*** Move to: c.txt\n@@\n-one\n+three",
            ):
                model.patch = patch(body)
                events = await observe(runtime, "next patch")
                assert isinstance(events[-1], TurnCompleted)
            assert len(prompts) == (2 if remember else 3)
            assert set(prompts[-1]["files"]) == {str(tmp_path / "a.txt"), str(tmp_path / "c.txt")}
            assert not (tmp_path / "a.txt").exists()
            assert (tmp_path / "c.txt").read_text() == "three\n"
            assert (tmp_path / "b.txt").read_text() == "two\n"
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_no_handler_is_an_error_without_writes(tmp_path, compiler):
    async def scenario():
        model = Model("direct")
        runtime = await create_runtime(tmp_path, compiler, model)
        try:
            assert isinstance((await observe(runtime, "patch"))[-1], TurnCompleted)
            output = [i for i in model.requests[-1].items if isinstance(i, ToolResultItem)][-1]
            assert "no host approval handler" in output.content
            assert not (tmp_path / "allowed.txt").exists()
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_cancel_while_waiting_for_patch_review_dismisses_owner(tmp_path, compiler, mode):
    async def scenario():
        model = Model(mode)
        runtime = await create_runtime(tmp_path, compiler, model)
        entered, finalized = asyncio.Event(), asyncio.Event()

        async def wait_for_host(request):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                finalized.set()

        runtime.set_execution_approval_handler(wait_for_host)
        consumer = asyncio.create_task(observe(runtime, "wait for review"))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            assert runtime._process_manager.approvals.router._pending
            assert not (tmp_path / "allowed.txt").exists()
            await asyncio.wait_for(runtime.cancel_active(), 5)
            events = await asyncio.wait_for(consumer, 5)
            assert isinstance(events[-1], TurnCancelled)
            assert finalized.is_set()
            assert not runtime._process_manager.approvals.router._pending
            assert not (tmp_path / "allowed.txt").exists()
            assert not [
                task
                for task in asyncio.all_tasks()
                if task.get_name().startswith("corki-sandbox-helper")
            ]
        finally:
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


def test_old_patch_compiler_rejects_approval_protocol(tmp_path):
    old = os.environ.get("CORKI_TEST_PRE_PATCH_APPROVAL_COMPILER")
    if not old:
        pytest.skip("requires real batch214 compiler")
    with pytest.raises(ValueError, match="unknown field `native_patch_approval`"):
        asyncio.run(
            file_operation(
                workspace_policy(Path(old), tmp_path),
                tmp_path,
                "patch",
                {"patch": patch("*** Add File: no.txt\n+no")},
            )
        )
    assert not (tmp_path / "no.txt").exists()
