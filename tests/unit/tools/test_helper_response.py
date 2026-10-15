import asyncio
import json
from types import SimpleNamespace

import pytest

from corki.config.instructions import ProjectInstructionsConfig
from corki.config.permissions import ExecutionPermissions
from corki.context.permissions import PermissionContext
from corki.execution import backend
from corki.execution.response import decode_helper_response
from corki.execution.rules import ExecutionRuleUpdates


@pytest.mark.parametrize(
    "payload",
    [
        b"null",
        b"[]",
        b"{}",
        b'{"ok":{},"error":null}',
        b'{"error":7}',
        b'{"ok":{},"ok":{}}',
        b'{"ok":{"profile":{"type":"disabled","type":"managed"}}}',
        b'{"ok":{"value":NaN}}',
        b'{"ok":{"value":1e999}}',
        b'{"ok":{"value":"\\ud800"}}',
        b'{"error":"\\ud800"}',
        b'{"ok":{}} trailing',
        b'{"ok":[]}',
    ],
)
def test_invalid_helper_result_rejected(payload):
    with pytest.raises(ValueError, match="test boundary"):
        decode_helper_response(payload, expected=dict, error_prefix="test boundary")


def test_string_payload_is_not_reinterpreted_as_json():
    text = '{"user-data":1,"user-data":2}'
    assert (
        decode_helper_response(json.dumps({"ok": text}).encode(), expected=str, error_prefix="file")
        == text
    )
    assert decode_helper_response(
        b'{"ok":{"list":[1,true,null,"text"]}}', expected=dict, error_prefix="native"
    ) == {"list": [1, True, None, "text"]}


@pytest.mark.parametrize("defect", ["mixed", "duplicate"])
def test_invalid_context_is_not_cached(tmp_path, monkeypatch, defect):
    async def scenario():
        value = {
            "permission_context": True,
            "text": "native text",
            "without_prefixes": "native text",
            "prefixes": [],
            "warnings": [],
            "environment": {
                "version": 1,
                "filesystem": '<filesystem><permission_profile type="disabled">'
                '<file_system type="unrestricted" /></permission_profile></filesystem>',
                "network": None,
            },
        }
        good = json.dumps({"ok": value}).encode()
        bad = (
            json.dumps({"error": "failed", "ok": value}).encode()
            if defect == "mixed"
            else good.replace(
                b'"permission_context": true',
                b'"permission_context": false,"permission_context": true',
            )
        )
        replies = [bad, good]

        async def helper(*args, **kwargs):
            return replies.pop(0)

        monkeypatch.setattr("corki.context.permissions.run_owned", helper)
        owner = PermissionContext()
        permissions = ExecutionPermissions(tmp_path / "compiler", tmp_path, '{"type":"disabled"}')
        with pytest.raises(ValueError):
            await owner.snapshot(permissions, tmp_path, honor_allow_rules=True)
        assert owner._snapshot is None and owner._key is None
        value = await owner.snapshot(permissions, tmp_path, honor_allow_rules=True)
        assert value.text == "native text" and not replies
        assert await owner.snapshot(permissions, tmp_path, honor_allow_rules=True) is value

    asyncio.run(scenario())


@pytest.mark.parametrize("consumer", ["compile", "memory", "file", "instructions"])
def test_all_backend_readers_use_single_result_contract(tmp_path, monkeypatch, consumer):
    async def scenario():
        calls = []

        async def helper(*args, **kwargs):
            calls.append(args)
            return b'{"error":"failure","ok":{}}'

        async def restricted(*args, **kwargs):
            return SimpleNamespace(command=["host-file-helper"], full_disk_read_access=False)

        monkeypatch.setattr(backend, "run_owned", helper)
        permissions = ExecutionPermissions(
            tmp_path / "compiler", tmp_path, '{"type":"external","network":"restricted"}'
        )
        with pytest.raises(ValueError, match="expected exactly one ok or error"):
            if consumer == "compile":
                await backend.sandbox_command(permissions, ["echo", "x"], tmp_path)
            elif consumer == "memory":
                await backend.derive_memory_permissions(permissions, tmp_path)
            elif consumer == "file":
                monkeypatch.setattr(backend, "_compile_native_file_helper", restricted)
                await backend.file_operation(permissions, tmp_path, "image", {})
            else:
                monkeypatch.setattr(backend, "_compile", restricted)
                await backend.read_project_instructions(
                    permissions, tmp_path, ProjectInstructionsConfig()
                )
        assert len(calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "payload",
    [
        b'{"error":"failed","ok":{"execpolicy_amendment_written":true,"execpolicy_amendment_published":true}}',
        b'{"ok":{"execpolicy_amendment_written":true,"execpolicy_amendment_published":false,"execpolicy_amendment_published":true}}',
    ],
)
def test_ambiguous_write_ack_warns_without_replay_or_publication(tmp_path, monkeypatch, payload):
    async def scenario():
        owner = ExecutionRuleUpdates()
        owner.path = tmp_path / "owned.rules"
        calls, warnings = [], []

        async def helper(*args, **kwargs):
            calls.append(args)
            # A write may have completed before its response became invalid.
            owner.path.write_text('prefix_rule(pattern=["touch"], decision="allow")\n')
            return payload

        async def warning(text):
            warnings.append(text)

        monkeypatch.setattr("corki.execution.rules.run_owned", helper)
        permissions = ExecutionPermissions(tmp_path / "compiler", tmp_path, '{"type":"disabled"}')
        await owner.persist(permissions, ("touch",), warning)
        assert len(calls) == len(warnings) == 1
        assert owner.path.exists() and owner.prefixes == ()
        assert "Failed to apply execpolicy amendment" in warnings[0]

    asyncio.run(scenario())
