"""Native lifecycle matching differs deliberately from MCP identity matching."""

import pytest

from corki.config import mcp_regex
from corki.core import hook_matchers
from corki.core.stop_hooks import command_identity


@pytest.mark.parametrize(
    "pattern,candidate,expected",
    [
        (None, "reviewer", True),
        ("", "reviewer", True),
        ("*", "reviewer", True),
        ("reviewer", "reviewer", True),
        ("review", "reviewer", False),
        ("worker|reviewer", "reviewer", True),
        ("worker|reviewer", "ReviewER", False),
        ("worker||reviewer", "", True),
        ("view.*", "reviewer", True),
        ("^view.*", "reviewer", False),
        (r"\p{Han}+", "审查", True),
        (r"\w+", "审查", True),
        (r"\d+", "١٢٣", True),
        ("(?i)k", "K", True),
    ],
)
def test_lifecycle_matcher_language(pattern, candidate, expected):
    hook_matchers.validate(pattern)
    assert hook_matchers.matches(pattern, candidate) is expected


@pytest.mark.parametrize("pattern", ["[", r"(x)\1", "(?=x)", 42, False])
def test_invalid_matcher_is_not_admitted(pattern):
    with pytest.raises(ValueError):
        hook_matchers.validate(pattern)


def test_hook_search_does_not_weaken_mcp_full_value_matching():
    assert hook_matchers.matches("view.*", "reviewer")
    assert not mcp_regex.matches("view.*", "reviewer")
    assert hook_matchers.matches(r"\w+", "审查")
    assert not mcp_regex.matches(r"\w+", "审查")


def test_matcher_and_event_are_authorization_identity():
    handler = {"type": "command", "command": "echo approved"}
    root = command_identity(handler)[0]
    assert command_identity(handler, matcher="ignored by Stop")[0] == root
    identities = {
        command_identity(handler, event_name="SubagentStop", matcher=matcher)[0]
        for matcher in (None, "", "*", "reviewer", "worker")
    }
    assert len(identities) == 5
    assert root not in identities


def test_regex_resource_failure_cannot_match_or_admit(monkeypatch):
    monkeypatch.setattr(mcp_regex, "_FUEL", 0)
    with pytest.raises(ValueError):
        hook_matchers.validate("view.*")
    assert not hook_matchers.matches("view.*", "reviewer")
