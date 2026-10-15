"""Patch retry authority, malformed evidence and unknown effect boundaries."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from corki.execution import backend
from corki.execution.approvals import ExecutionApprovals
from corki.execution.patch_result import PatchResult
from corki.execution.patch_retry import PatchRetryPlan, parse_patch_retry


def outcome(*, denied=True):
    return {
        "version": 2,
        "success": False,
        "output": "first failed; no rollback performed",
        "execution": {
            "exit_code": 1,
            "stdout": "",
            "stderr": "permission denied",
            "sandbox_denied": denied,
        },
        "delta": {
            "version": 1,
            "exact": False,
            "changes": [
                {
                    "path": "/first",
                    "change": {"kind": "add", "content": "one", "overwritten_content": None},
                },
            ],
        },
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("exit_code", True),
        ("exit_code", 0),
        ("stdout", []),
        ("stderr", None),
        ("sandbox_denied", 1),
    ],
)
def test_malformed_execution_evidence_cannot_authorize_retry(field, value):
    raw = outcome()
    raw["execution"][field] = value
    with pytest.raises(ValueError, match="execution evidence"):
        PatchResult.parse(json.dumps(raw))


@pytest.mark.parametrize("mutation", ["capability", "backend", "argv", "flag", "policy"])
def test_invalid_retry_plan_fails_closed(tmp_path, mutation):
    compiler = tmp_path / "compiler"
    value = {
        "native_patch_retry": 1,
        "sandbox": "seatbelt",
        "patch_retry": {"sandbox": "seatbelt", "command": [str(compiler), "--corki-fs-helper"]},
    }
    policy = '"on-request"'
    assert parse_patch_retry(value, compiler, policy).command == (
        str(compiler),
        "--corki-fs-helper",
    )
    if mutation == "capability":
        value["native_patch_retry"] = True
    elif mutation == "backend":
        value["patch_retry"]["sandbox"] = "none"
    elif mutation == "argv":
        value["patch_retry"]["command"][0] = "/different/compiler"
    elif mutation == "flag":
        value["patch_retry"]["command"][1] = "arbitrary"
    else:
        policy = '"never"'
    with pytest.raises(ValueError):
        parse_patch_retry(value, compiler, policy)


@pytest.mark.parametrize(
    "failure", [ValueError("lost retry output"), asyncio.CancelledError(), None]
)
def test_retry_failure_preserves_prefix_and_never_attempts_a_third_time(
    tmp_path, monkeypatch, failure
):
    async def scenario():
        async def compile(*args, **kwargs):
            return SimpleNamespace(
                command=["sandboxed"],
                patch_authority={},
                patch_retry=PatchRetryPlan("seatbelt", ("fixed-helper",)),
            )

        calls, reviews = [], []

        async def run(argv, data, **kwargs):
            args = json.loads(data)["arguments"]
            calls.append(args)
            if args["prepare"]:
                raw = {
                    "version": 1,
                    "requires_approval": False,
                    "patch": "normalized",
                    "cwd": str(tmp_path),
                    "files": ["/first"],
                    "changes": [{"path": "/first", "change": {"kind": "add", "content": "one"}}],
                }
            elif not args["retry"]:
                raw = outcome()
            else:
                assert argv == ["fixed-helper"] and args["approved"] is True
                assert args["patch"] == "normalized"
                if failure is not None:
                    raise failure
                raw = outcome()  # Even a second classified denial cannot loop.
            return json.dumps({"ok": json.dumps(raw)}).encode()

        class Approvals:
            async def authorize_patch(self, review, **kwargs):
                reviews.append(kwargs)

        monkeypatch.setattr(backend, "_compile_native_file_helper", compile)
        monkeypatch.setattr(backend, "run_owned", run)
        if isinstance(failure, asyncio.CancelledError):
            with pytest.raises(asyncio.CancelledError):
                await backend.patch_operation(
                    None, tmp_path, {"patch": "original"}, approvals=Approvals()
                )
        else:
            result = await backend.patch_operation(
                None, tmp_path, {"patch": "original"}, approvals=Approvals()
            )
            delta = json.loads(result.delta_json)
            assert result.success is False and delta["exact"] is False
            assert delta["changes"][0]["path"] == "/first"
            if failure is not None:
                assert "Do not automatically retry" in result.output
                assert len(delta["changes"]) == 1
            else:
                assert len(delta["changes"]) == 2
        assert len(reviews) == 1 and len(calls) == 3
        assert reviews[0]["retry"]["committed_delta"] == outcome()["delta"]

    asyncio.run(scenario())


def test_session_cache_cannot_replace_fresh_retry_consent():
    async def scenario():
        owner = ExecutionApprovals()
        owner._session.add(("patch", "local", "/first"))
        with pytest.raises(ValueError, match="no host approval handler"):
            await owner.authorize_patch({"files": ["/first"]}, call_id="call", retry={})
        await owner.authorize_patch({"files": ["/first"]}, call_id="call")

    asyncio.run(scenario())


def test_combined_delta_overflow_is_explicitly_inexact(monkeypatch):
    from corki.protocol import patches

    raw = outcome()
    raw["delta"]["exact"] = True
    raw["delta"]["changes"][0]["change"]["content"] = "x" * 200
    first = PatchResult.parse(json.dumps(raw))
    monkeypatch.setattr(patches, "MAX_PATCH_RECORD_BYTES", len(first.delta_json.encode()) + 1)
    combined = first.append_attempt(first)
    assert json.loads(combined.delta_json) == {"version": 1, "exact": False, "changes": []}
    assert "Combined delta exceeded its transport limit" in combined.output
