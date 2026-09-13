"""A native early denial can retry once, before background process ownership transfers."""

import asyncio
import json
import shlex
import sys

import pytest
from test_execution_approval_cancel import observe
from test_execution_policy_live_inheritance import Model, run, runtime_for
from test_execution_policy_live_inheritance import compiler as compiler

from corki.config import CorkiSettings
from corki.config.execution_requirements import ExecutionRequirementsLayer
from corki.config.permissions import ExecutionPermissions
from corki.execution.owned_process import run_owned
from corki.execution.retry import is_sandbox_denial
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCancelled, TurnCompleted
from corki.protocol.items import ToolResultItem


@pytest.mark.parametrize(
    "sandbox,code,output,expected",
    [
        ("none", 1, "Permission denied", False),
        ("seatbelt", 0, "Permission denied", False),
        ("seatbelt", 1, "ordinary failure", False),
        ("seatbelt", 127, "command not found", False),
        ("seatbelt", 127, "Permission denied", True),
        ("seccomp", 2, "Operation not permitted", True),
        ("windows_sandbox", 1, "Read-only file system", True),
    ],
)
def test_pinned_denial_predicate_preserves_order_and_backend_identity(
    tmp_path, compiler, sandbox, code, output, expected
):
    permissions = ExecutionPermissions(compiler, tmp_path, '{"type":"read-only"}')
    assert asyncio.run(is_sandbox_denial(permissions, sandbox, code, output)) is expected


def granular(enabled=True):
    return json.dumps(
        {
            "granular": {
                "sandbox_approval": enabled,
                "rules": True,
                "mcp_elicitations": False,
            }
        }
    )


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_timeout_with_denial_text_cannot_authorize_retry(tmp_path, compiler, mode, monkeypatch):
    launches = capture_spawns(monkeypatch)

    async def classify(*args):
        raise AssertionError("timed out commands must not enter denial classification")

    monkeypatch.setattr("corki.tools.builtin.process_retry.is_sandbox_denial", classify)

    async def scenario():
        runtime, model = setup(tmp_path, compiler, mode, approval=granular(), timeout=0.05)
        try:
            await run(runtime, model, {"cmd": "printf 'Permission denied'; sleep 3"})
            assert len(launches) == 1 and launches[0][0].returncode is not None
            assert not runtime._process_manager._sessions and not runtime._process_manager._starting
            results = [i for i in model.requests[-1].items if isinstance(i, ToolResultItem)]
            assert "timed out" in results[-1].content.lower()
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def setup(
    root,
    compiler,
    mode,
    *,
    approval='"untrusted"',
    profile='{"type":"read-only"}',
    requirements=(),
    timeout=None,
):
    model = Model(mode)
    settings = CorkiSettings(
        working_directory=root,
        skills_enabled=False,
        model="fixture",
        tool_mode=mode,
        model_contexts=(ModelContextInfo(model="fixture"),),
        command_timeout_seconds=timeout,
        execution_permissions=ExecutionPermissions(
            compiler,
            root,
            profile,
            requirements=requirements,
            approval_policy_json=approval,
        ),
    )
    return runtime_for(root, compiler, model, settings=settings), model


def capture_spawns(monkeypatch):
    from corki.tools.builtin import process

    launches = []
    original = process._spawn

    async def spawn(*args, **kwargs):
        child = await original(*args, **kwargs)
        launches.append((child, kwargs["prepared_argv"]))
        return child

    monkeypatch.setattr(process, "_spawn", spawn)
    return launches


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("tty", [False, True])
def test_approved_early_denial_retries_once_before_handoff(
    tmp_path, compiler, mode, tty, monkeypatch
):
    launches = capture_spawns(monkeypatch)

    async def scenario():
        runtime, model = setup(tmp_path, compiler, mode)
        target = tmp_path / "approved-retry"
        prompts = []

        async def approve(request):
            prompts.append(request)
            assert not target.exists()
            runtime.respond_execution_approval(request.request_id, "accept")

        runtime.set_execution_approval_handler(approve)
        try:
            await run(runtime, model, {"cmd": "touch " + shlex.quote(str(target)), "tty": tty})
            assert target.exists()
            assert len(prompts) == 1
            assert len(launches) == 2
            assert "sandbox-exec" in launches[0][1][0]
            assert "sandbox-exec" not in launches[1][1][0]
            assert all(child.returncode is not None for child, _ in launches)
            results = [i for i in model.requests[-1].items if isinstance(i, ToolResultItem)]
            assert results and not results[-1].is_error
            assert not runtime._process_manager._sessions
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("phase", ["grace", "classification", "approval"])
@pytest.mark.parametrize("action", ["cancel", "close"])
def test_cancel_or_close_joins_private_attempt_before_retry(
    tmp_path, compiler, mode, phase, action, monkeypatch
):
    launches = capture_spawns(monkeypatch)

    async def scenario():
        entered, finalized = asyncio.Event(), asyncio.Event()
        runtime, model = setup(tmp_path, compiler, mode, approval=granular())
        target = tmp_path / "never-retried"
        helpers = []
        script = "import time; time.sleep(60)"
        original_spawn = asyncio.create_subprocess_exec

        async def capture(*args, **kwargs):
            child = await original_spawn(*args, **kwargs)
            if args == (sys.executable, "-c", script):
                helpers.append(child)
                entered.set()
            return child

        monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)

        async def classify(*args):
            try:
                await run_owned([sys.executable, "-c", script], b"", cwd=tmp_path, output_limit=100)
            finally:
                finalized.set()

        async def review(request):
            entered.set()

        if phase == "classification":
            monkeypatch.setattr("corki.tools.builtin.process_retry.is_sandbox_denial", classify)
        elif phase == "grace":
            wait = runtime._process_manager._wait_session

            async def wait_in_startup(session, seconds):
                if seconds == 0.15:
                    entered.set()
                await wait(session, seconds)

            monkeypatch.setattr(runtime._process_manager, "_wait_session", wait_in_startup)
        runtime.set_execution_approval_handler(review)
        model.command = {
            "cmd": "sleep 60" if phase == "grace" else "touch " + shlex.quote(str(target))
        }
        pending = asyncio.create_task(observe(runtime, "cancel retry"))
        try:
            async with asyncio.timeout(10):
                await entered.wait()
                assert len(launches) == 1 and not runtime._process_manager._sessions
                if action == "close":
                    await runtime.aclose()
                else:
                    await runtime.cancel_active()
                events = await pending
            assert isinstance(events[-1], TurnCancelled)
            assert len(launches) == 1 and launches[0][0].returncode is not None
            assert not target.exists()
            assert not runtime._process_manager._sessions and not runtime._process_manager._starting
            assert not runtime._process_manager.approvals.router._pending
            if phase == "classification":
                assert finalized.is_set()
                assert len(helpers) == 1 and helpers[0].returncode is not None
        finally:
            await runtime.aclose()
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_classifier_failure_never_replays_completed_first_attempt(
    tmp_path, compiler, mode, monkeypatch
):
    launches = capture_spawns(monkeypatch)

    async def fail(*args):
        raise ValueError("fixture classifier failed")

    monkeypatch.setattr("corki.tools.builtin.process_retry.is_sandbox_denial", fail)

    async def scenario():
        runtime, model = setup(tmp_path, compiler, mode, approval=granular())
        try:
            await run(runtime, model, {"cmd": "printf 'Permission denied'; exit 1"})
            assert len(launches) == 1 and launches[0][0].returncode is not None
            assert not runtime._process_manager._sessions and not runtime._process_manager._starting
            results = [i for i in model.requests[-1].items if isinstance(i, ToolResultItem)]
            assert "fixture classifier failed" in results[-1].content
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize(
    "case", ["never", "on-request", "granular-disabled", "read-deny", "unsandboxed"]
)
def test_policy_and_actual_backend_can_forbid_automatic_retry(
    tmp_path, compiler, mode, case, monkeypatch
):
    launches = capture_spawns(monkeypatch)

    async def scenario():
        target = tmp_path / "write"
        requirements = (
            (
                ExecutionRequirementsLayer(
                    "organization",
                    json.dumps(
                        {"permissions": {"filesystem": {"deny_read": [str(tmp_path / "secret")]}}}
                    ),
                ),
            )
            if case == "read-deny"
            else ()
        )
        policy = (
            granular(False)
            if case == "granular-disabled"
            else json.dumps(case)
            if case in {"never", "on-request"}
            else '"untrusted"'
        )
        runtime, model = setup(
            tmp_path,
            compiler,
            mode,
            approval=policy,
            requirements=requirements,
            profile='{"type":"disabled"}' if case == "unsandboxed" else '{"type":"read-only"}',
        )
        prompts = []

        async def approve(request):
            prompts.append(request)
            runtime.respond_execution_approval(request.request_id, "accept")

        runtime.set_execution_approval_handler(approve)
        try:
            await run(runtime, model, {"cmd": "touch " + shlex.quote(str(target))})
            assert target.exists() is (case == "unsandboxed")
            assert len(launches) == 1
            assert len(prompts) == int(case in {"read-deny", "unsandboxed"})
            results = [i for i in model.requests[-1].items if isinstance(i, ToolResultItem)]
            assert results and not results[-1].is_error
            assert not runtime._process_manager._sessions
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("action", ["accept", "decline", "cancel"])
def test_unapproved_retry_requires_explicit_review_after_first_process_exit(
    tmp_path, compiler, mode, action, monkeypatch
):
    launches = capture_spawns(monkeypatch)

    async def scenario():
        runtime, model = setup(tmp_path, compiler, mode, approval=granular())
        target = tmp_path / "reviewed-retry"
        prompts = []
        reviewed = []

        async def review(request):
            prompts.append(request)
            assert "retry without sandbox?" in request.params["message"]
            assert len(launches) == 1 and launches[0][0].returncode is not None
            assert not runtime._process_manager._sessions and runtime._process_manager._starting
            assert not target.exists()
            reviewed.append(True)
            runtime.respond_execution_approval(request.request_id, action)

        runtime.set_execution_approval_handler(review)
        model.command = {"cmd": "touch " + shlex.quote(str(target))}
        try:
            async with asyncio.timeout(10):
                events = await observe(runtime, "retry review")
            assert isinstance(events[-1], TurnCancelled if action == "cancel" else TurnCompleted)
            assert target.exists() is (action == "accept")
            assert len(launches) == (2 if action == "accept" else 1) and len(prompts) == 1
            assert reviewed == [True]
            assert not runtime._process_manager._sessions and not runtime._process_manager._starting
            assert not runtime._process_manager.approvals.router._pending
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("kind", ["ordinary-exit", "repeated-denial", "late-denial"])
def test_no_retry_loop_or_replay_after_handoff(tmp_path, compiler, mode, kind, monkeypatch):
    launches = capture_spawns(monkeypatch)

    async def scenario():
        runtime, model = setup(tmp_path, compiler, mode, approval=granular())
        prompts = []

        async def review(request):
            prompts.append(request)
            runtime.respond_execution_approval(request.request_id, "accept")

        runtime.set_execution_approval_handler(review)
        command = {
            "ordinary-exit": "printf ordinary-failure; exit 1",
            "repeated-denial": "printf 'Permission denied'; exit 1",
            "late-denial": "sleep 0.8; printf 'Permission denied'; exit 1",
        }[kind]
        try:
            await run(runtime, model, {"cmd": command, "yield_time_ms": 1})
            if kind == "late-denial":
                sessions = runtime._process_manager._sessions
                assert len(sessions) == 1
                observation = await runtime._process_manager.write_stdin(
                    next(iter(sessions)), "", yield_seconds=2
                )
                assert observation.exit_code == 1 and observation.session_id is None
                assert "Permission denied" in observation.output
            assert len(launches) == (2 if kind == "repeated-denial" else 1)
            assert len(prompts) == int(kind == "repeated-denial")
            assert all(child.returncode is not None for child, _ in launches)
            assert not runtime._process_manager._sessions
            results = [i for i in model.requests[-1].items if isinstance(i, ToolResultItem)]
            assert results and not results[-1].is_error
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
