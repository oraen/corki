"""Invalid native Result envelopes never authorize another model-command attempt."""

import asyncio
import json
import shlex

import pytest
from test_execution_policy_live_inheritance import compiler as compiler
from test_execution_policy_live_inheritance import run
from test_sandbox_denial_retry import capture_spawns, setup

from corki.protocol.events import TurnFailed


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize(
    "payload",
    [
        b'{"error":"classifier failed","ok":{"revision":1,"sandbox_denial":true}}',
        b'{"ok":{"revision":1,"sandbox_denial":false,"sandbox_denial":true}}',
    ],
)
def test_invalid_denial_cannot_retry_approved_command(
    tmp_path, compiler, mode, payload, monkeypatch
):
    launches = capture_spawns(monkeypatch)

    async def scenario():
        runtime, model = await setup(tmp_path, compiler, mode)
        target = tmp_path / "must-not-exist"
        classifications, approvals = [], []

        async def classify(*args, **kwargs):
            classifications.append(args)
            return payload

        async def approve(request):
            approvals.append(request)
            runtime.respond_execution_approval(request.request_id, "accept")

        monkeypatch.setattr("corki.execution.retry.run_owned", classify)
        runtime.set_execution_approval_handler(approve)
        try:
            await run(runtime, model, {"cmd": "touch " + shlex.quote(str(target))})
            assert len(classifications) == len(approvals) == 1
            assert len(launches) == 1
            assert not target.exists()
            assert launches[0][0].returncode is not None
            assert not runtime._process_manager._sessions
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("defect", ["mixed", "duplicate"])
def test_invalid_permission_context_fails_before_model_and_can_recover(
    tmp_path, compiler, mode, defect, monkeypatch
):
    from corki.context import permissions as context

    original = context.run_owned
    calls = []
    broken = True

    async def helper(*args, **kwargs):
        calls.append(args)
        response = json.loads(await original(*args, **kwargs))
        if broken and defect == "mixed":
            response["error"] = "permission renderer failed"
        wire = json.dumps(response)
        if broken and defect == "duplicate":
            assert '"permission_context": true' in wire
            wire = wire.replace(
                '"permission_context": true',
                '"permission_context": false,"permission_context": true',
                1,
            )
        return wire.encode()

    monkeypatch.setattr(context, "run_owned", helper)

    async def scenario():
        nonlocal broken
        runtime, model = await setup(tmp_path, compiler, mode)
        events = []
        try:
            try:
                async for event in runtime.stream("invalid permission context"):
                    events.append(event)
            except ValueError:
                pass
            assert not model.requests and len(calls) == 1
            assert events and isinstance(events[-1], TurnFailed)
            broken = False
            await run(runtime, model)
            assert model.requests and len(calls) == 2
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
