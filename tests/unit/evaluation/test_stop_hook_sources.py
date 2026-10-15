"""JSON source identity, deduplication and authority are independent of TOML."""

import json

from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core.stop_hooks import command_identity, discover


def test_same_folder_json_snapshot_is_discovered_once(tmp_path):
    handler = {"type": "command", "command": "echo ok"}
    fingerprint = command_identity(handler)[0]
    key = f"{tmp_path / 'hooks.json'}:stop:0:0"
    snapshot = json.dumps({"hooks": {"Stop": [{"hooks": [handler]}]}})
    configuration = LocalConfigState(
        (
            ConfigLayer(
                tmp_path / "first.toml",
                "user",
                hooks_json=snapshot,
                contents=f"[hooks.state.{json.dumps(key)}]\ntrusted_hash={json.dumps(fingerprint)}\n",
            ),
            ConfigLayer(tmp_path / "second.toml", "user", hooks_json=snapshot),
        )
    )
    commands, warnings = discover(configuration)
    assert not warnings
    assert len(commands) == 1
    assert commands[0].key == key


def test_bad_json_does_not_discard_independently_approved_toml(tmp_path):
    handler = {"type": "command", "command": "echo ok"}
    fingerprint = command_identity(handler)[0]
    source = tmp_path / "config.toml"
    key = f"{source}:stop:0:0"
    configuration = LocalConfigState(
        (
            ConfigLayer(
                source,
                "user",
                hooks_json="{",
                contents="[[hooks.Stop]]\n[[hooks.Stop.hooks]]\ntype='command'\ncommand='echo ok'\n"
                + f"[hooks.state.{json.dumps(key)}]\ntrusted_hash={json.dumps(fingerprint)}\n",
            ),
        )
    )
    commands, warnings = discover(configuration)
    assert len(commands) == 1
    assert commands[0].key == key
    assert any("JSON" in warning for warning in warnings)
