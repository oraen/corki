"""Shared policy writes keep per-session paths and cancellation-joined publication."""

import asyncio
import json
from dataclasses import FrozenInstanceError

import pytest

from corki.config.exec_policy import ExecPolicySnapshot
from corki.config.permissions import ExecutionPermissions
from corki.execution import rules
from corki.execution.rules import ExecPolicyHandle, ExecutionRuleUpdates

_PUBLISHED = b'{"ok":{"execpolicy_amendment_written":true,"execpolicy_amendment_published":true}}'


def pair(tmp_path):
    parent, child = ExecutionRuleUpdates(), ExecutionRuleUpdates()
    parent.path, child.path = tmp_path / "parent.rules", tmp_path / "child.rules"
    snapshot = ExecPolicySnapshot((tmp_path,), (), ())
    handle = parent.capture(snapshot)
    child.inherit(handle)
    return parent, child, handle


def test_concurrent_child_write_joins_cancelled_parent_without_changing_destinations(
    tmp_path, monkeypatch
):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def compile(argv, payload, **kwargs):
            calls.append(json.loads(payload)["append_execpolicy"])
            if len(calls) == 1:
                entered.set()
                await release.wait()
            return _PUBLISHED

        monkeypatch.setattr(rules, "run_owned", compile)
        parent, child, handle = pair(tmp_path)
        permissions = ExecutionPermissions(tmp_path / "compiler", tmp_path, '{"type":"read-only"}')
        first = asyncio.create_task(parent.persist(permissions, ("touch",)))
        second = None
        try:
            await entered.wait()
            second = asyncio.create_task(child.persist(permissions, ("mkdir",)))
            await asyncio.sleep(0)
            first.cancel()
            await asyncio.sleep(0)
            first.cancel()
            await asyncio.sleep(0)
            assert not first.done() and len(calls) == 1
            assert parent.prefixes == child.prefixes == ()
            release.set()
            results = await asyncio.gather(first, second, return_exceptions=True)
            assert isinstance(results[0], asyncio.CancelledError) and results[1] is None
            assert [call["path"] for call in calls] == [str(parent.path), str(child.path)]
            assert calls[0]["current_policy"]["approved_prefixes"] == []
            assert calls[1]["current_policy"]["approved_prefixes"] == [["touch"]]
            assert parent.prefixes == child.prefixes == (("touch",), ("mkdir",))
            assert handle.snapshot.sources == ()
            with pytest.raises(FrozenInstanceError):
                handle.snapshot = ExecPolicySnapshot((), (), ())
        finally:
            release.set()
            await asyncio.gather(
                first, *([second] if second is not None else []), return_exceptions=True
            )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "response", [b'{"error":"denied"}', b'{"ok":{}}', b'{"ok":{"execpolicy_amendment_written":1}}']
)
def test_failed_write_does_not_publish_or_poison_shared_lock(tmp_path, monkeypatch, response):
    async def scenario():
        parent, child, _ = pair(tmp_path)
        warnings = []

        async def compile(argv, payload, **kwargs):
            request = json.loads(payload)["append_execpolicy"]
            return response if request["path"] == str(parent.path) else _PUBLISHED

        async def warn(message):
            warnings.append(message)

        monkeypatch.setattr(rules, "run_owned", compile)
        permissions = ExecutionPermissions(tmp_path / "compiler", tmp_path, '{"type":"read-only"}')
        await parent.persist(permissions, ("touch",), warn)
        assert len(warnings) == 1 and child.prefixes == ()
        await child.persist(permissions, ("mkdir",), warn)
        assert parent.prefixes == child.prefixes == (("mkdir",),)
        assert len(warnings) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("bad", [None, {}, "not a snapshot"])
def test_handle_rejects_untyped_snapshots(tmp_path, bad):
    async def scenario():
        parent, _, handle = pair(tmp_path)
        with pytest.raises(ValueError, match="captured snapshot"):
            parent.capture(bad)
        with pytest.raises(ValueError, match="owned rule state"):
            ExecPolicyHandle(handle.snapshot, object())

    asyncio.run(scenario())
