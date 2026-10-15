"""Native optional metadata decoding is separate from per-dependency resolution."""

import json

import pytest

from corki.skills.models import SkillToolDependency
from corki.skills.policy import load_metadata


def load(tmp_path, tools):
    sidecar = tmp_path / "agents" / "openai.yaml"
    sidecar.parent.mkdir(exist_ok=True)
    sidecar.write_text(
        json.dumps(
            {
                "dependencies": {"tools": tools},
                "policy": {"allow_implicit_invocation": False},
            }
        )
    )
    return load_metadata(tmp_path / "SKILL.md")


@pytest.mark.parametrize(
    "invalid",
    [
        {},
        {"type": "mcp"},
        {"value": "pending"},
        {"type": None, "value": "pending"},
        {"type": "mcp", "value": " \n\u2003"},
        {"type": "x" * 65, "value": "pending"},
        {"type": "mcp", "value": "界" * 1025},
    ],
)
def test_unresolvable_dependency_preserves_policy_and_valid_siblings(tmp_path, invalid):
    metadata = load(tmp_path, [invalid, {"type": "mcp", "value": "pending"}])
    assert metadata.allow_implicit_invocation is False
    assert metadata.dependencies == (SkillToolDependency("mcp", "pending"),)


def test_dependency_fields_use_rust_whitespace_and_unicode_character_limits(tmp_path):
    metadata = load(
        tmp_path,
        [
            {
                "type": " \tMcP\u2003",
                "value": "\u2003pending\n server ",
                "description": "界" * 1024,
                "transport": "\tstreamable_http\n",
                "command": " cmd \t --flag\u00a0arg ",
                "url": "\u001chttps://example.test\u001c",
                "oauth": {"callbackPort": 3118},
            }
        ],
    )
    assert metadata.dependencies == (
        SkillToolDependency(
            "McP",
            "pending server",
            "界" * 1024,
            "streamable_http",
            "cmd --flag arg",
            "\u001chttps://example.test\u001c",
            3118,
        ),
    )
    assert metadata.allow_implicit_invocation is False


@pytest.mark.parametrize(
    "field,limit",
    [
        ("description", 1024),
        ("transport", 64),
        ("command", 1024),
        ("url", 1024),
    ],
)
@pytest.mark.parametrize("blank", [False, True])
def test_unresolvable_optional_field_does_not_drop_dependency(tmp_path, field, limit, blank):
    value = "\n\u2003" if blank else "界" * (limit + 1)
    metadata = load(tmp_path, [{"type": "mcp", "value": "pending", field: value}])
    assert metadata.allow_implicit_invocation is False
    assert metadata.dependencies == (SkillToolDependency("mcp", "pending"),)


@pytest.mark.parametrize(
    "invalid",
    [
        {"type": []},
        {"value": {}},
        {"oauth": {"callbackPort": -1}},
        {"oauth": {"callbackPort": 1, "callback_port": 2}},
    ],
)
def test_decode_error_discards_whole_optional_metadata(tmp_path, invalid):
    metadata = load(tmp_path, [invalid, {"type": "mcp", "value": "pending"}])
    assert metadata.allow_implicit_invocation is True
    assert metadata.dependencies == ()
