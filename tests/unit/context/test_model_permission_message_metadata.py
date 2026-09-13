"""Catalog metadata and both durable snapshot codecs preserve optional text."""

import asyncio
import json
from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.config.model_context import parse_model_contexts
from corki.config.permissions import ExecutionPermissions
from corki.context.permissions import PermissionContext
from corki.core.checkpoint import checkpoint_serializer
from corki.core.model_settings import capture_model_settings
from corki.protocol.context import ModelContextInfo
from corki.protocol.permission_messages import ModelPermissionMessages
from corki.protocol.settings import ModelSettingsSnapshot


@pytest.mark.parametrize("value", [None, "", "custom {{ network_access }}"])
def test_messages_survive_business_and_checkpoint_snapshots(tmp_path, value):
    settings = CorkiSettings(
        working_directory=tmp_path,
        model="fixture",
        model_contexts=parse_model_contexts(
            {
                "fixture": {
                    "model_messages": {
                        "approvals": {"never": value},
                        "permissions": {"danger_full_access": value},
                    }
                }
            }
        ),
    )
    original = capture_model_settings(settings)
    restored = ModelSettingsSnapshot.from_payload(json.loads(json.dumps(original.to_payload())))
    assert restored == original
    serializer = checkpoint_serializer()
    assert serializer.loads_typed(serializer.dumps_typed(original)) == original
    assert restored.model_info.permission_messages.never == value


def test_old_model_snapshot_without_messages_remains_readable(tmp_path):
    original = capture_model_settings(CorkiSettings(working_directory=tmp_path, model="fixture"))
    payload = original.to_payload()
    payload["model_info"].pop("permission_messages")
    restored = ModelSettingsSnapshot.from_payload(payload)
    assert restored == original and restored.model_info.permission_messages is None


@pytest.mark.parametrize("section", ["approvals", "permissions"])
@pytest.mark.parametrize("value", [[], "invalid", True])
def test_invalid_catalog_message_sections_are_rejected(section, value):
    with pytest.raises(ValueError, match=section):
        parse_model_contexts({"fixture": {"model_messages": {section: value}}})


@pytest.mark.parametrize(
    "field",
    [
        "on_request",
        "on_request_auto_review",
        "never",
        "unless_trusted",
        "danger_full_access",
        "workspace_write",
        "read_only",
    ],
)
def test_invalid_individual_message_fields_are_rejected(field):
    with pytest.raises(ValueError, match=field):
        ModelPermissionMessages(**{field: 1})


def test_model_info_rejects_untyped_message_object():
    with pytest.raises(ValueError, match="permission_messages"):
        ModelContextInfo("fixture", permission_messages={"never": "text"})


def test_message_changes_do_not_mutate_previously_captured_snapshot(tmp_path):
    settings = CorkiSettings(
        working_directory=tmp_path,
        model="fixture",
        model_contexts=(
            ModelContextInfo("fixture", permission_messages=ModelPermissionMessages(never="old")),
        ),
    )
    original = capture_model_settings(settings)
    changed = replace(
        settings,
        model_contexts=(
            ModelContextInfo("fixture", permission_messages=ModelPermissionMessages(never="new")),
        ),
    )
    assert capture_model_settings(changed).model_info.permission_messages.never == "new"
    assert original.model_info.permission_messages.never == "old"


@pytest.mark.parametrize("ack", [None, False, 1, "true"])
def test_model_overrides_require_typed_native_ack(tmp_path, monkeypatch, ack):
    async def scenario():
        async def compiler(*args, **kwargs):
            return json.dumps(
                {
                    "ok": {
                        "permission_context": True,
                        "model_permission_messages": ack,
                        "text": "old default",
                        "without_prefixes": "old default",
                        "prefixes": [],
                        "warnings": [],
                    }
                }
            ).encode()

        monkeypatch.setattr("corki.context.permissions.run_owned", compiler)
        with pytest.raises(ValueError, match="model permission messages"):
            await PermissionContext().snapshot(
                ExecutionPermissions(tmp_path / "compiler", tmp_path, '{"type":"disabled"}'),
                tmp_path,
                honor_allow_rules=True,
                messages=ModelPermissionMessages(never=""),
            )

    asyncio.run(scenario())
