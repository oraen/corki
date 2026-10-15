"""Modern positional identity matchers must preserve native full-value semantics."""

from dataclasses import replace

import pytest

from corki.config import MCPServerSettings, mcp_regex
from corki.config.mcp_matchers import MCPCommandMatcher, MCPValueMatcher
from corki.config.mcp_requirements import MCPRequirements, MCPServerSource


def test_typed_command_matches_all_positional_args_and_exact_executable():
    policy = MCPRequirements.from_mapping(
        {
            "mcp_servers": {
                "docs": {
                    "identity": {
                        "command": {
                            "executable": "company-cli",
                            "args": [
                                {"match": "exact", "value": "mcp"},
                                {"match": "regex", "expression": r"https://[a-z]+\.example\.com"},
                            ],
                        }
                    }
                }
            }
        }
    )
    server = MCPServerSettings(
        "docs", "stdio", command="company-cli", args=("mcp", "https://pricing.example.com")
    )
    source = MCPServerSource("docs")
    assert policy.allows(server, source)
    for changes in (
        {"command": "/usr/bin/company-cli"},
        {"args": server.args[::-1]},
        {"args": (*server.args, "--verbose")},
        {"args": ("mcp", "https://pricing.example.com/extra")},
    ):
        assert not policy.allows(replace(server, **changes), source)


@pytest.mark.parametrize(
    "matcher",
    [
        {"match": "prefix", "value": "https://api.example.com/"},
        {
            "match": "regex",
            "expression": r"https://api\.example\.com|https://api\.example\.com/mcp",
        },
    ],
)
def test_typed_url_full_value_matching_with_later_regex_alternative(matcher):
    policy = MCPRequirements.from_mapping({"mcp_servers": {"docs": {"identity": {"url": matcher}}}})
    assert policy.allows(
        MCPServerSettings("docs", "http", url="https://api.example.com/mcp"),
        MCPServerSource("docs"),
    )
    assert not policy.allows(
        MCPServerSettings("docs", "http", url="https://api.example.com.evil/mcp"),
        MCPServerSource("docs"),
    )


@pytest.mark.parametrize(
    "expression,candidate,expected",
    [
        (r"\d+", "123", True),
        (r"\d+", "١٢٣", False),
        (r"\w+", "abc_1", True),
        (r"\w+", "é", False),
        (r"\s", "\u00a0", False),
        (r"(?i)k", "K", True),
        (r"(?i)k", "K", False),
        (r"[^β]", "雪", True),
        (r"[[:alpha:]]+", "abc", True),
        (r"(?i)a+(?-i)b+", "Aabb", True),
        (r"(?i)a+(?-i)b+", "AaBB", False),
        (r"(?x)[ a ]", " ", False),
        (r"(?x)[ a ]", "a", True),
        (r"(?mR)^foo$\r\n", "foo\r\n", True),
        (r"\b{start}foo\b{end}", "foo", True),
        (r"\b{start-half}foo\b{end-half}", "foo", True),
        (r"(?<a.b>foo)", "foo", True),
        (r"\x{1F4A9}", "💩", True),
        ("", "", True),
        ("", "x", False),
        ("mcp$", "mcp\n", False),
        (r"(?:a+)+", "a" * 20_000 + "!", False),
    ],
)
def test_fixed_engine_semantics_not_python_re(expression, candidate, expected):
    assert MCPValueMatcher("regex", expression).matches(candidate) is expected


@pytest.mark.parametrize(
    "expression",
    [
        r"(?=foo)foo",
        r"(?<=a)b",
        r"(a)\1",
        r"\p{L}",
        r"[a&&b]",
        r"\123",
        "[",
        "(?x)mcp # trailing comment",
    ],
)
def test_native_invalid_or_unwrappable_patterns_rejected_before_publication(expression):
    with pytest.raises(ValueError):
        MCPValueMatcher("regex", expression)


@pytest.mark.parametrize(
    "identity",
    [
        {"url": {"match": "prefix", "value": "x", "unknown": True}},
        {"url": {"match": "regex", "value": "x"}},
        {"url": {"match": {}, "value": "x"}},
        {"url": {"match": "exact", "value": 3}},
        {"url": {"match": "prefix", "value": "x"}, "extra": "no"},
        {"command": {"executable": "x"}},
        {"command": {"executable": "x", "args": [], "extra": "no"}},
        {"command": {"executable": "x", "args": "wrong"}},
    ],
)
def test_typed_forms_reject_unknown_missing_or_wrong_type_fields(identity):
    with pytest.raises(ValueError):
        MCPRequirements.from_mapping({"mcp_servers": {"docs": {"identity": identity}}})


def test_legacy_precedence_does_not_validate_ignored_typed_sibling():
    policy = MCPRequirements.from_mapping(
        {
            "mcp_servers": {
                "docs": {
                    "identity": {
                        "command": "server",
                        "url": {"match": "regex", "expression": "["},
                    }
                }
            }
        }
    )
    assert policy.allows(
        MCPServerSettings("docs", "stdio", command="server"), MCPServerSource("docs")
    )


def test_caller_list_mutation_does_not_change_positional_policy():
    args = [MCPValueMatcher("prefix", "trusted-")]
    matcher = MCPCommandMatcher("server", args)
    args.clear()
    assert matcher.matches(
        MCPServerSettings("docs", "stdio", command="server", args=("trusted-one",))
    )
    assert not matcher.matches(MCPServerSettings("docs", "stdio", command="server"))


def test_engine_fuel_exhaustion_never_becomes_authorization(monkeypatch):
    matcher = MCPValueMatcher("regex", "trusted")
    monkeypatch.setattr(mcp_regex, "_FUEL", 0)
    assert not matcher.matches("trusted")
    with pytest.raises(ValueError, match="engine"):
        MCPValueMatcher("regex", "trusted")


def test_guest_has_no_host_capability_imports():
    _, module = mcp_regex._engine()
    assert module.imports == []


def test_every_guest_store_is_closed_on_success_and_trap(monkeypatch):
    stores = []
    original = mcp_regex.wasmtime.Store

    class Store(original):
        def __init__(self, *args):
            super().__init__(*args)
            stores.append(self)

    monkeypatch.setattr(mcp_regex.wasmtime, "Store", Store)
    assert mcp_regex.matches("trusted", "trusted")
    monkeypatch.setattr(mcp_regex, "_FUEL", 0)
    assert not mcp_regex.matches("trusted", "trusted")
    assert len(stores) == 2
    for store in stores:
        with pytest.raises(ValueError, match="closed"):
            store.ptr()
