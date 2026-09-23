"""Net committed patch diffs in direct/nested Runtime and cold recovery."""

import asyncio
import hashlib
import json
import subprocess
import time
from types import SimpleNamespace

import pytest
from test_bundled_execution import compiler as compiler
from test_execution_approval_cancel import observe
from test_filesystem_helper_runtime import workspace_policy
from test_patch_approvals import Model, create_runtime, patch
from test_recovery_and_concurrency import AnswerModel, NullSink

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.models import ModelCompleted
from corki.protocol.events import TurnCancelled, TurnCompleted
from corki.protocol.ids import new_tool_call_id, new_turn_id
from corki.protocol.items import ToolCallItem, UserMessageItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.sessions import TurnRecord, TurnStatus


class Patches(Model):
    def __init__(self, mode, bodies):
        super().__init__(mode)
        self.bodies = iter(bodies)

    async def stream(self, request):
        self.requests.append(request)
        body = next(self.bodies, None)
        if body is None:
            yield ModelCompleted(())
            return
        arguments = {"patch": patch(body)} if isinstance(body, str) else body
        turn = request.items[-1].turn_id
        call = ToolCall(new_tool_call_id(), "apply_patch", arguments)
        if self.mode != "direct":
            call = ToolCall(
                new_tool_call_id(),
                "exec",
                None,
                input_kind="freeform",
                raw_arguments="text(await tools.apply_patch(" + json.dumps(arguments) + ")); ",
            )
        yield ModelCompleted((ToolCallItem(call, turn, new_step_id()),))


def diffs(events):
    return [event.unified_diff for event in events if type(event).__name__ == "TurnDiff"]


def added(name, content):
    oid = hashlib.sha1(f"blob {len(content.encode())}\0".encode() + content.encode()).hexdigest()
    lines = content.splitlines()
    count = str(len(lines)) if len(lines) != 1 else ""
    suffix = f",{count}" if count else ""
    return (
        f"diff --git a/{name} b/{name}\nnew file mode 100644\n"
        f"index {'0' * 40}..{oid}\n--- /dev/null\n+++ b/{name}\n@@ -0,0 +1{suffix} @@\n"
        + "".join("+" + line + "\n" for line in lines)
    )


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_multi_step_net_diff_and_next_turn_baseline(tmp_path, compiler, mode):
    async def scenario():
        model = Patches(
            mode, ["*** Add File: a.txt\n+foo", "*** Update File: a.txt\n@@\n foo\n+bar"]
        )
        runtime = await create_runtime(
            tmp_path, compiler, model, policy=workspace_policy(compiler, tmp_path)
        )
        try:
            events = await observe(runtime, "edit")
            assert isinstance(events[-1], TurnCompleted)
            assert (tmp_path / "a.txt").read_text() == "foo\nbar\n"
            assert (
                diffs(events) == [added("a.txt", "foo\n")] * 2 + [added("a.txt", "foo\nbar\n")] * 3
            )
            model.bodies = iter(["*** Update File: a.txt\n@@\n-foo\n+baz"])
            events = await observe(runtime, "next turn")
            assert len(diffs(events)) == 3 and len(set(diffs(events))) == 1
            assert "new file mode" not in diffs(events)[0]
            assert "-foo\n+baz\n" in diffs(events)[0]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("ending", ["delete", "inexact", "reject", "bad_arguments"])
def test_clears_net_zero_or_inexact_but_preserves_prewrite_rejection(
    tmp_path, compiler, mode, ending
):
    async def scenario():
        (tmp_path / "directory").mkdir()
        ending_patch = {
            "delete": "*** Delete File: a.txt",
            "inexact": "*** Add File: b.txt\n+written\n*** Add File: directory\n+failure",
            "reject": "*** Update File: missing.txt\n@@\n-before\n+after",
            "bad_arguments": {"wrong": "invalid"},
        }[ending]
        model = Patches(mode, ["*** Add File: a.txt\n+foo", ending_patch])
        runtime = await create_runtime(
            tmp_path, compiler, model, policy=workspace_policy(compiler, tmp_path)
        )
        try:
            events = await observe(runtime, "edit")
            assert isinstance(events[-1], TurnCompleted)
            unchanged = ending in {"reject", "bad_arguments"}
            expected = [added("a.txt", "foo\n")] * (4 if unchanged else 2)
            if not unchanged:
                expected.append("")
            assert diffs(events) == expected
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_cold_resume_uses_committed_ledger_without_rereading_or_replaying_workspace(
    tmp_path, compiler
):
    async def scenario():
        settings = CorkiSettings(
            tmp_path,
            skills_enabled=False,
            execution_permissions=workspace_policy(compiler, tmp_path),
        )
        database = tmp_path / "recovery.db"
        first = await LangGraphRuntime.acreate(
            settings=settings,
            model=AnswerModel(),
            database_path=database,
            home_path=tmp_path / "home",
        )
        await first._ensure_ready()
        thread, turn = first.thread_id, new_turn_id()
        user = UserMessageItem("once", turn)
        repository = first._repository
        await repository.save_turn(TurnRecord(turn, thread, TurnStatus.RUNNING, "once"))
        await repository.append_items(thread, (user,))
        call = ToolCall(
            new_tool_call_id(), "apply_patch", {"patch": patch("*** Add File: a.txt\n+original")}
        )
        await repository.commit_model_step(
            thread, turn, 0, ModelCompleted((ToolCallItem(call, turn, new_step_id()),))
        )
        state = _initial_state(thread, turn, first._settings, user)
        state["request_tools"] = first._registry.specs()
        await first._graph._execute_one(
            state, SimpleNamespace(context=GraphRunContext(events=NullSink())), call
        )
        await first.aclose()
        (tmp_path / "a.txt").write_text("external edit\n")
        second = await LangGraphRuntime.acreate(
            settings=settings,
            model=AnswerModel(),
            database_path=database,
            home_path=tmp_path / "home",
            thread_id=thread,
        )
        try:
            events = [event async for event in second.resume_pending()]
            assert isinstance(events[-1], TurnCompleted)
            assert diffs(events) == [added("a.txt", "original\n")] * 3
            assert (tmp_path / "a.txt").read_text() == "external edit\n"
        finally:
            await second.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_cancellation_after_write_clears_prior_diff(tmp_path, compiler, monkeypatch, mode):
    from corki.execution import backend

    async def scenario():
        entered = asyncio.Event()
        actual = backend.run_owned
        writes = 0

        async def delayed(argv, payload, **kwargs):
            nonlocal writes
            result = await actual(argv, payload, **kwargs)
            request = json.loads(payload)
            if request.get("operation") == "patch" and request["arguments"].get("prepare") is False:
                writes += 1
                if writes == 2:
                    entered.set()
                    await asyncio.Event().wait()
            return result

        monkeypatch.setattr(backend, "run_owned", delayed)
        model = Patches(mode, ["*** Add File: a.txt\n+foo", "*** Add File: a.txt\n+second"])
        runtime = await create_runtime(
            tmp_path, compiler, model, policy=workspace_policy(compiler, tmp_path)
        )
        try:
            task = asyncio.create_task(observe(runtime, "edit"))
            await asyncio.wait_for(entered.wait(), 10)
            await runtime.cancel_active()
            events = await asyncio.wait_for(task, 10)
            assert isinstance(events[-1], TurnCancelled)
            assert diffs(events)[0] == added("a.txt", "foo\n") and diffs(events)[-1] == ""
            assert (tmp_path / "a.txt").read_text() == "second\n" and writes == 2
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_native_large_rewrite_is_bounded_and_content_exact(tmp_path, compiler):
    from corki.core.turn_diff import render_diff

    old = "".join(f"old line {number:05}\n" for number in range(48_000))
    new = "".join(f"new line {number:05}\n" for number in range(48_000))
    path = tmp_path / "large.txt"
    path.write_text(old)
    started = time.monotonic()
    diff = asyncio.run(render_diff(compiler, tmp_path, path, old, path, new))
    assert time.monotonic() - started < 3
    applied = subprocess.run(
        ["git", "apply", "-"], input=diff, text=True, cwd=tmp_path, capture_output=True, check=False
    )
    assert applied.returncode == 0, applied.stderr
    assert path.read_text() == new


def test_interrupted_patch_cleanup_does_not_wait_for_abandoned_event_sink(tmp_path, compiler):
    async def scenario():
        model = Patches("direct", [])
        runtime = await create_runtime(
            tmp_path, compiler, model, policy=workspace_policy(compiler, tmp_path)
        )
        await runtime._ensure_ready()
        thread, turn = runtime.thread_id, new_turn_id()
        state = _initial_state(thread, turn, runtime._settings, UserMessageItem("edit", turn))
        state["request_tools"] = runtime._registry.specs()

        class Sink:
            abandoned = False

            async def emit(self, event):
                if self.abandoned:
                    await asyncio.Event().wait()

        sink = Sink()
        context = GraphRunContext(events=sink)
        node = SimpleNamespace(context=context)
        try:
            first = ToolCall(
                new_tool_call_id(), "apply_patch", {"patch": patch("*** Add File: a.txt\n+one")}
            )
            await runtime._graph._execute_one(state, node, first)
            assert context.turn_diff.diff is not None
            second = ToolCall(
                new_tool_call_id(), "apply_patch", {"patch": patch("*** Add File: b.txt\n+unknown")}
            )
            await runtime._repository.claim_tool_call(thread, turn, second)
            context.turn_diff.started(second.id)
            sink.abandoned = True
            result = await asyncio.wait_for(
                runtime._graph._execute_one(state, node, second, interrupted=True), 1
            )
            assert result.is_error and not (tmp_path / "b.txt").exists()
            assert context.turn_diff.final_clear(thread, turn, aborted=True).unified_diff == ""
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_legacy_sdk_failure_cannot_claim_an_exact_empty_delta(tmp_path, monkeypatch):
    import corki.tools.builtin.patch as patch_module

    def partial(*args):
        (tmp_path / "uncertain.txt").write_text("written")
        raise OSError("rollback failed")

    monkeypatch.setattr(patch_module, "_apply_operations", partial)

    async def scenario():
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(tmp_path, skills_enabled=False, execution_permissions=None),
            model=Patches("direct", ["*** Add File: a.txt\n+one"]),
            home_path=tmp_path / "home",
            database_path=tmp_path / "state.db",
        )
        try:
            events = await observe(runtime, "edit")
            completed = [event for event in events if type(event).__name__ == "ToolCallCompleted"]
            assert completed[0].is_error and completed[0].patch_delta_json is None
            assert (tmp_path / "uncertain.txt").read_text() == "written"
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
