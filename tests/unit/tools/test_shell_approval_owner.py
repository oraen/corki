"""Local command review owns its response token, cancellation and exact session cache."""

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

import corki
from corki.execution.approvals import ExecApprovalRequirement, ExecutionApprovals
from corki.mcp.elicitation import ElicitationRouter


@pytest.mark.parametrize(
    "module", ["corki.execution.backend", "corki.mcp", "corki.tools.builtin.process"]
)
def test_shell_approval_owner_is_constructible_in_cold_import_orders(module):
    root = Path(corki.__file__).resolve().parent.parent
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            f"import sys; sys.path.insert(0, {str(root)!r}); import {module}; "
            "from corki.execution.approvals import ExecutionApprovals; ExecutionApprovals()",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("change", ["same", "argv", "cwd", "tty", "policy", "intent", "reason"])
def test_session_approval_reuses_only_matching_local_execution(tmp_path, change):
    async def scenario():
        approvals, prompts = ExecutionApprovals(), []
        unrelated = ElicitationRouter()

        async def handler(request):
            prompts.append(request)
            with pytest.raises(ValueError, match="Unknown"):
                unrelated.respond(request.server_name, request.request_id, "accept")
            approvals.router.respond(
                request.server_name, request.request_id, "accept", content={"remember": True}
            )

        approvals.router.handler = handler
        await approvals.authorize(
            ExecApprovalRequirement("review", None),
            ["printf", "one"],
            tmp_path,
            call_id="one",
            tty=False,
        )
        await approvals.authorize(
            ExecApprovalRequirement("review", ("new rule",) if change == "policy" else None),
            ["printf", "two" if change == "argv" else "one"],
            tmp_path / "other" if change == "cwd" else tmp_path,
            call_id="two",
            tty=change == "tty",
            sandbox_permissions="require_escalated" if change == "intent" else "use_default",
            justification="Different presentation" if change == "reason" else None,
        )
        assert len(prompts) == (1 if change in {"same", "reason"} else 2)
        assert not approvals.router._pending

    asyncio.run(scenario())


def test_cancel_joins_delivery_rejects_stale_response_and_does_not_cache(tmp_path):
    async def scenario():
        approvals = ExecutionApprovals()
        entered, closed = asyncio.Event(), asyncio.Event()
        requests = []

        async def handler(request):
            requests.append(request)
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()

        approvals.router.handler = handler
        task = asyncio.create_task(
            approvals.authorize(
                ExecApprovalRequirement(None, None), ["printf"], tmp_path, call_id="c", tty=False
            )
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set() and not approvals.router._pending and not approvals._session
        with pytest.raises(ValueError, match="Unknown"):
            approvals.router.respond("local-shell", requests[0].request_id, "accept")

    asyncio.run(scenario())


@pytest.mark.parametrize("other_executable", [False, True])
def test_canonical_command_cache_keeps_original_executable_identity(tmp_path, other_executable):
    async def scenario():
        approvals, prompts = ExecutionApprovals(), []

        async def handler(request):
            prompts.append(request)
            approvals.router.respond(
                request.server_name, request.request_id, "accept", content={"remember": True}
            )

        approvals.router.handler = handler
        requirement = ExecApprovalRequirement(None, None, ("printf", "value"))
        await approvals.authorize(
            requirement, ["/bin/bash", "-c", "printf value"], tmp_path, call_id="a", tty=False
        )
        await approvals.authorize(
            requirement,
            ["/bin/zsh" if other_executable else "/bin/bash", "-c", "printf   value"],
            tmp_path,
            call_id="b",
            tty=False,
        )
        assert len(prompts) == (2 if other_executable else 1)

    asyncio.run(scenario())
