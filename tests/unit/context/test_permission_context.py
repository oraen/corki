import asyncio
import json

import pytest

from corki.config import CorkiSettings
from corki.config.permissions import ExecutionPermissions
from corki.context.permissions import PermissionContext
from corki.protocol.wire_json import loads_wire


@pytest.mark.parametrize("enabled", [False, True])
def test_permission_context_flag_is_loaded_from_top_level_config(tmp_path, enabled):
    path = tmp_path / "config.toml"
    path.write_text("include_permissions_instructions = " + str(enabled).lower() + "\n")
    settings = CorkiSettings.for_directory(tmp_path, config_file=path)
    assert settings.include_permissions_instructions is enabled


@pytest.mark.parametrize("value", [0, 1, "false", None])
def test_permission_context_flag_rejects_untyped_values(tmp_path, value):
    with pytest.raises(ValueError, match="include_permissions_instructions"):
        CorkiSettings(working_directory=tmp_path, include_permissions_instructions=value)


@pytest.mark.parametrize(
    "failure",
    [
        "missing",
        "untyped",
        "empty_rule",
        "invalid_text",
        "bytes",
        "tokens",
        "environment_missing",
        "environment_version",
        "environment_xml",
        "environment_bytes",
        "environment_section",
        "environment_dtd",
    ],
)
def test_invalid_native_context_is_not_published_or_cached(tmp_path, monkeypatch, failure):
    async def scenario():
        valid = {
            "permission_context": True,
            "text": "context",
            "without_prefixes": "context",
            "prefixes": [],
            "warnings": [],
            "environment": {"version": 1, "filesystem": "<filesystem />", "network": None},
        }
        value = dict(valid)
        if failure == "missing":
            value.pop("permission_context")
        elif failure == "untyped":
            value["permission_context"] = 1
        elif failure == "empty_rule":
            value["prefixes"] = [[]]
        elif failure == "invalid_text":
            value["text"] = None
        elif failure == "bytes":
            value["text"] = "a" * 30_001
        elif failure == "tokens":
            value["text"] = "中" * 7000
        elif failure == "environment_missing":
            value.pop("environment")
        else:
            environment = dict(valid["environment"])
            value["environment"] = environment
            if failure == "environment_version":
                environment["version"] = True
            elif failure == "environment_xml":
                environment["filesystem"] = "<filesystem>"
            elif failure == "environment_bytes":
                environment["filesystem"] = "<filesystem>" + "a" * 30_001 + "</filesystem>"
            elif failure == "environment_section":
                environment["filesystem"] = "<unrelated />"
            else:
                environment["filesystem"] = "<!DOCTYPE filesystem><filesystem />"
        calls = []

        async def compiler(argv, data, **kwargs):
            calls.append(loads_wire(data.decode()))
            return json.dumps({"ok": value}).encode()

        monkeypatch.setattr("corki.context.permissions.run_owned", compiler)
        permissions = ExecutionPermissions(tmp_path / "compiler", tmp_path, '{"type":"disabled"}')
        owner = PermissionContext()
        with pytest.raises(ValueError):
            await owner.snapshot(permissions, tmp_path, honor_allow_rules=True)
        value = valid
        snapshot = await owner.snapshot(permissions, tmp_path, honor_allow_rules=True)
        assert snapshot.text == "context" and len(calls) == 2
        assert await owner.snapshot(permissions, tmp_path, honor_allow_rules=True) is snapshot
        assert len(calls) == 2

    asyncio.run(scenario())


def test_cancelled_context_compilation_does_not_publish_a_snapshot(tmp_path, monkeypatch):
    async def scenario():
        entered, finished = asyncio.Event(), asyncio.Event()

        async def compiler(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                finished.set()

        monkeypatch.setattr("corki.context.permissions.run_owned", compiler)
        permissions = ExecutionPermissions(tmp_path / "compiler", tmp_path, '{"type":"disabled"}')
        owner = PermissionContext()
        task = asyncio.create_task(owner.snapshot(permissions, tmp_path, honor_allow_rules=True))
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set() and owner._snapshot is None

    asyncio.run(scenario())
