import asyncio
import json
import os
import shlex
import socket
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from corki.config import CorkiSettings
from corki.config.execution_requirements import ExecutionRequirementsLayer
from corki.config.permissions import ExecutionPermissions
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    ToolCallItem,
    ToolResultItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall


def engine():
    path = os.environ.get("CORKI_TEST_SANDBOX_COMPILER")
    if not path or sys.platform != "darwin":
        pytest.skip("requires explicit native sandbox compiler and macOS")
    return Path(path)


@pytest.mark.parametrize("mode", ["managed", "disabled", "external"])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("stale_base", [False, True])
@pytest.mark.parametrize("managed_approval", [False, True])
def test_background_worker_derives_actual_permissions_before_tools(
    tmp_path,
    mode,
    nested,
    stale_base,
    managed_approval,
):
    compiler = engine()
    profile = {
        "managed": {"type": "workspace-write", "network_access": True},
        "disabled": {"type": "disabled"},
        "external": {"type": "external", "network": "restricted"},
    }[mode]
    policy = ExecutionPermissions(compiler, tmp_path, json.dumps(profile))
    if managed_approval:
        policy = replace(
            policy,
            approval_policy_json='"on-request"',
            requirements=(
                ExecutionRequirementsLayer(
                    "organization", '{"allowed_approval_policies":["on-request"]}'
                ),
            ),
        )
    outside = tmp_path / "parent-workspace-file"

    async def scenario(port):
        requests = []
        observed = []

        class Main:
            async def stream(self, request):
                yield ModelCompleted(
                    (
                        AssistantMessageItem(
                            "main done",
                            request.items[-1].turn_id,
                            new_step_id(),
                        ),
                    )
                )

            async def aclose(self):
                pass

        script = f"""
import json, pathlib, socket
results = {{}}
for name, path in [('memory', pathlib.Path('child-owned.txt')),
                   ('parent', pathlib.Path({str(outside)!r}))]:
    try:
        path.write_text('fixture')
        results[name] = True
    except OSError:
        results[name] = False
try:
    socket.create_connection(('127.0.0.1', {port}), timeout=1).close()
    results['network'] = True
except OSError:
    results['network'] = False
print('EFFECTS=' + json.dumps(results), flush=True)
"""

        class Memory:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    args = {
                        "cmd": shlex.join([sys.executable, "-I", "-c", script]),
                        "login": False,
                        "yield_time_ms": 1000,
                    }
                    call = ToolCall(new_tool_call_id(), "exec_command", args)
                    if nested:
                        call = ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            raw_arguments="text((await tools.exec_command("
                            + json.dumps(args)
                            + ")).output)",
                            input_kind="freeform",
                        )
                    item = ToolCallItem(call, turn, step)
                else:
                    result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    observed.append(result.content)
                    item = AssistantMessageItem(
                        json.dumps(
                            {
                                "memory": "verified",
                                "memory_summary": "verified",
                                "skills": [],
                            }
                        ),
                        turn,
                        step,
                    )
                yield ModelCompleted((item,))

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                execution_permissions=policy,
                tool_mode="code_mode_only" if nested else "direct",
                memories_consolidation_model="fixture",
            ),
            database_path=tmp_path / "sessions.db",
            memory_root=tmp_path / "memories",
            model=Main(),
            memory_model=Memory(),
        )
        if stale_base:
            # The effective caller and background service's base config are
            # distinct native inputs. Passing only base settings loses authority.
            other_profile = (
                {"type": "disabled"}
                if mode == "managed"
                else {
                    "type": "read-only",
                    "network_access": False,
                }
            )
            runtime._memory_service._settings = replace(
                runtime._memory_service._settings,
                execution_permissions=ExecutionPermissions(
                    compiler,
                    tmp_path,
                    json.dumps(other_profile),
                ),
            )
        try:
            events = [event async for event in runtime.stream("work")]
            assert isinstance(events[-1], TurnCompleted)
            report = await runtime._memory_service.wait()
            assert report.consolidated and not report.failed, runtime._memory_service.warnings
            assert len(observed) == 1 and "EFFECTS=" in observed[0], observed
            # Parent policy allows only OnRequest in the managed variant, but
            # phase-two's independent Never-only constraint must survive worker
            # Runtime startup and reach the actual model request.
            context = "\n".join(
                item.content for item in requests[0].items if isinstance(item, ContextItem)
            )
            assert "Approval policy is currently never" in context
            # The External branch delegates enforcement to its caller; this test
            # checks no local reinterpretation, not caller-owned sandbox security.
            expected = {"memory": True, "parent": mode != "managed", "network": mode != "managed"}
            assert json.dumps(expected) in observed[0], observed
            assert outside.exists() is (mode != "managed")
            assert "verified" in (tmp_path / "memories/MEMORY.md").read_text()
        finally:
            await runtime.aclose()

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(10)
        asyncio.run(scenario(listener.getsockname()[1]))


@pytest.mark.parametrize("failure", ["missing_backend", "invalid_profile"])
def test_worker_policy_failure_isolated_and_claim_released_without_sampling(
    tmp_path,
    monkeypatch,
    failure,
    memory_worker_state_dirs,
):
    from corki.memory import agent

    compiler = tmp_path / "missing-compiler" if failure == "missing_backend" else engine()
    profile = {"type": "read-only", "network_access": "invalid"}
    permissions = ExecutionPermissions(compiler, tmp_path, json.dumps(profile))
    parent_permissions = (
        None
        if failure == "missing_backend"
        else ExecutionPermissions(compiler, tmp_path, '{"type":"read-only"}')
    )
    copies, requests, staged = memory_worker_state_dirs, [], []
    prepare = agent._prepare
    for_worker = agent.MemoryPermissionSnapshot.for_worker

    async def observe_policy(snapshot, root):
        assert root == tmp_path / "memories" and root.is_dir()
        # Parent admission is valid. Inject malformed authority only at the
        # worker derivation boundary; malformed parent policy now fails before
        # sampling and cannot demonstrate background failure isolation.
        return await for_worker(replace(snapshot, parent=permissions), root)

    def observe_prepare(root, *args):
        staged.append(root)
        return prepare(root, *args)

    monkeypatch.setattr(agent.MemoryPermissionSnapshot, "for_worker", observe_policy)
    monkeypatch.setattr(agent, "_prepare", observe_prepare)

    async def scenario():
        class Main:
            async def stream(self, request):
                yield ModelCompleted(
                    (
                        AssistantMessageItem(
                            "parent unaffected",
                            request.items[-1].turn_id,
                            new_step_id(),
                        ),
                    )
                )

            async def aclose(self):
                pass

        class Memory(Main):
            async def stream(self, request):
                requests.append(request)
                async for event in super().stream(request):
                    yield event

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                execution_permissions=parent_permissions,
            ),
            database_path=tmp_path / "sessions.db",
            memory_root=tmp_path / "memories",
            model=Main(),
            memory_model=Memory(),
        )
        try:
            events = [event async for event in runtime.stream("parent continues")]
            assert isinstance(events[-1], TurnCompleted)
            report = await runtime._memory_service.wait()
            assert report.failed == 1 and not report.consolidated
            assert not requests, "invalid worker authority must precede model sampling"
            assert not staged, "invalid worker authority must precede copying inputs"
            assert copies and all(not copy.exists() for copy in copies)
            with sqlite3.connect(tmp_path / "sessions.db") as connection:
                row = connection.execute(
                    "SELECT status,ownership_token,lease_until,error,retry_at "
                    "FROM memory_jobs WHERE kind='memory_consolidate_global'"
                ).fetchone()
            assert row[:4] == ("failed", None, None, "failed_sandbox_policy"), row
            assert row[4] is not None
            assert any("failed_sandbox_policy" in w for w in runtime._memory_service.warnings)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_shutdown_during_worker_policy_preparation_joins_before_releasing_claim(
    tmp_path,
    monkeypatch,
    memory_worker_state_dirs,
):
    from corki.memory import permissions as memory_permissions

    async def scenario():
        entered, cancelled = asyncio.Event(), asyncio.Event()
        calls = []

        async def held(parent, root):
            calls.append(root)
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        monkeypatch.setattr(memory_permissions, "derive_memory_permissions", held)
        for_worker = memory_permissions.MemoryPermissionSnapshot.for_worker

        async def child_authority(snapshot, root):
            # Exercise the owned worker derivation/cancellation window without
            # pretending sys.executable is a valid parent policy compiler.
            parent = ExecutionPermissions(Path(sys.executable), tmp_path, '{"type":"disabled"}')
            return await for_worker(replace(snapshot, parent=parent), root)

        monkeypatch.setattr(
            memory_permissions.MemoryPermissionSnapshot, "for_worker", child_authority
        )

        class Model:
            async def stream(self, request):
                yield ModelCompleted(
                    (
                        AssistantMessageItem(
                            "parent",
                            request.items[-1].turn_id,
                            new_step_id(),
                        ),
                    )
                )

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                memories_enabled=True,
                skills_enabled=False,
            ),
            database_path=tmp_path / "sessions.db",
            memory_root=tmp_path / "memories",
            model=Model(),
            memory_model=Model(),
        )
        try:
            events = [event async for event in runtime.stream("parent")]
            assert isinstance(events[-1], TurnCompleted)
            await asyncio.wait_for(entered.wait(), 3)
            await runtime.aclose()
            assert cancelled.is_set()
            assert calls and all(root == tmp_path / "memories" and root.is_dir() for root in calls)
            assert memory_worker_state_dirs and all(
                not path.exists() for path in memory_worker_state_dirs
            )
            with sqlite3.connect(tmp_path / "sessions.db") as connection:
                row = connection.execute(
                    "SELECT status,ownership_token,lease_until,error "
                    "FROM memory_jobs WHERE kind='memory_consolidate_global'"
                ).fetchone()
            assert row == ("failed", None, None, "memory consolidation cancelled"), row
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_background_pass_captures_parent_before_the_scheduled_task_runs(tmp_path, monkeypatch):
    from corki.memory.pipeline import MemoryRunReport

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        seen = []
        first = ExecutionPermissions(Path(sys.executable), tmp_path, '{"type":"disabled"}')
        second = ExecutionPermissions(Path(sys.executable), tmp_path, '{"type":"read-only"}')

        class Model:
            async def stream(self, request):
                raise AssertionError("not sampled by this scheduling test")
                yield

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                memories_enabled=True,
                skills_enabled=False,
                execution_permissions=first,
            ),
            database_path=tmp_path / "sessions.db",
            memory_root=tmp_path / "memories",
            model=Model(),
            memory_model=Model(),
        )
        service = runtime._memory_service

        async def observed(thread, *, parent_permissions):
            seen.append(parent_permissions.parent)
            if len(seen) == 2:
                entered.set()
            await release.wait()
            return MemoryRunReport()

        monkeypatch.setattr(service, "run_once", observed)
        try:
            service.start(runtime.thread_id)
            service._settings = replace(service._settings, execution_permissions=second)
            service.start(runtime.thread_id)
            await asyncio.wait_for(entered.wait(), 3)
            assert seen == [first, second]
            release.set()
            await service.wait()
        finally:
            release.set()
            await runtime.aclose()

    asyncio.run(scenario())
