"""Golden hashes from the pinned Rust TOML -> canonical JSON -> SHA-256 chain."""

import pytest

from corki.core import stop_hooks
from corki.core.stop_hooks import command_identity


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        (
            {"command": "echo ok"},
            "61d84ddbe34686344e05fe5a36be7f8d35413ba336ba68452a9ba6ccf0181ff4",
        ),
        (
            {"command": "printf '中文\\n'", "timeout": 0, "statusMessage": "检查"},
            "0ec1cfc47502dd7979632ace454e69ac28612faa363734eac048db8a760ba2c8",
        ),
        (
            {"command": "echo ok", "statusMessage": ""},
            "cc6379c0476e10a1b356fff9e96dc7a3729c815ef420a71c6d66ef1db01a0f8f",
        ),
    ],
)
def test_stop_command_fingerprint_matches_rust(fields, expected):
    assert command_identity({"type": "command", **fields})[0] == "sha256:" + expected


def test_ignored_fields_and_explicit_defaults_do_not_change_approval():
    basic = {"type": "command", "command": "echo ok"}
    assert (
        command_identity(basic)[0]
        == command_identity(
            {
                **basic,
                "timeout": 600,
                "async": False,
                "additionalContextLimit": 7,
                "unknown": "ignored by typed native handler",
            }
        )[0]
    )
    assert command_identity(basic)[0] != command_identity({**basic, "command": "echo changed"})[0]
    assert command_identity(basic)[0] != command_identity({**basic, "timeout": 1})[0]


@pytest.mark.parametrize(
    "fields",
    [{"statusMessage": 1}, {"additionalContextLimit": -1}, {"async": "false"}, {"timeout": True}],
)
def test_invalid_typed_fields_are_not_silently_ignored(fields):
    with pytest.raises(ValueError):
        command_identity({"type": "command", "command": "echo ok", **fields})


@pytest.mark.parametrize("platform", ["posix", "nt"])
@pytest.mark.parametrize("field", ["commandWindows", "command_windows"])
def test_platform_command_is_selected_before_hashing(monkeypatch, platform, field):
    basic = {"type": "command", "command": "echo default"}
    with monkeypatch.context() as patch:
        patch.setattr(stop_hooks.os, "name", platform)
        expected = "echo windows" if platform == "nt" else "echo default"
        fingerprint, normalized = command_identity({**basic, field: "echo windows"})
        assert normalized["command"] == expected
        assert fingerprint == command_identity({**basic, "command": expected})[0]
        assert field not in normalized


@pytest.mark.parametrize("platform", ["posix", "nt"])
def test_only_selected_command_must_be_nonempty(monkeypatch, platform):
    with monkeypatch.context() as patch:
        patch.setattr(stop_hooks.os, "name", platform)
        valid = {"type": "command", "command": "echo ok", "commandWindows": ""}
        if platform == "nt":
            valid.update(command="", commandWindows="echo ok")
        assert command_identity(valid)[1]["command"] == "echo ok"
        invalid = {**valid, "command" if platform == "posix" else "commandWindows": " "}
        with pytest.raises(ValueError, match="empty"):
            command_identity(invalid)
