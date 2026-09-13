"""Native cache identity, generation, expiry, opt-out and ownership contracts."""

import gc
import weakref
from dataclasses import replace

import httpx
import pytest

from corki.config import MCPEnvVar, MCPServerSettings
from corki.mcp.initialization import validate_initialization
from corki.mcp.runtime_environment import MCPHTTPEnvironment
from corki.mcp.tool_catalog_cache import CatalogSnapshot, MCPToolCatalogCache


def server(**options):
    return MCPServerSettings("docs", "http", url="https://docs.test", **options)


def snapshot(name="lookup"):
    return CatalogSnapshot(
        (
            {
                "name": name,
                "inputSchema": {"type": "object"},
                "annotations": {"readOnlyHint": True, "destructiveHint": False},
            },
        ),
        "instructions",
    )


@pytest.mark.parametrize("name", ["docs", "codex_apps"])
def test_snapshot_copies_and_strips_all_annotations(name):
    cache = MCPToolCatalogCache()
    entry = cache.context(replace(server(), name=name))
    original = snapshot()
    entry.publish_if_newest(entry.begin_fetch(), original)
    assert original.definitions[0]["annotations"]["readOnlyHint"]
    original.definitions[0]["inputSchema"]["type"] = "array"
    original.definitions[0]["name"] = "changed-source"
    captured = entry.current_tools_or()
    assert "annotations" not in captured.definitions[0]
    captured.definitions[0]["name"] = "mutated"
    assert entry.current_tools_or().definitions[0]["name"] == "lookup"
    assert entry.current_tools_or().definitions[0]["inputSchema"] == {"type": "object"}
    assert entry.current_tools_or().instructions == "instructions"


def test_publication_orders_accepted_not_started_fetches():
    entry = MCPToolCatalogCache().context(server())
    older, newer = entry.begin_fetch(), entry.begin_fetch()
    entry.publish_if_newest(older, snapshot("old"))
    assert entry.current_tools_or().definitions[0]["name"] == "old"
    entry.publish_if_newest(newer, snapshot("new"))
    entry.publish_if_newest(older, snapshot("late"))
    assert entry.current_tools_or().definitions[0]["name"] == "new"


def test_ttl_fallback_and_sticky_opt_out():
    now = [0.0]
    entry = MCPToolCatalogCache(clock=lambda: now[0]).context(server())
    entry.publish_if_newest(entry.begin_fetch(), snapshot())
    captured = entry.current_tools_or()
    now[0] = 1800
    assert entry.current_tools_or() is not None
    now[0] += 0.001
    assert entry.current_tools_or() is None
    assert entry.current_tools_or(captured) == captured
    entry.disable()
    entry.publish_if_newest(entry.begin_fetch(), snapshot("new"))
    assert entry.current_tools_or(captured) is None


def test_shared_grace_does_not_restart_but_changed_grace_resets():
    entry = MCPToolCatalogCache().context(server())
    assert entry.optional_startup_deadline(100, 1) == 100
    assert entry.optional_startup_deadline(101, 1) == 100
    assert entry.optional_startup_deadline(102, 2) == 102
    entry.publish_if_newest(entry.begin_fetch(), CatalogSnapshot((), None))
    assert entry.optional_startup_deadline(103, 2) == 103
    entry.disable()
    assert entry.optional_startup_deadline(104, 2) == 104


def test_lru_eviction_does_not_revoke_active_context():
    cache = MCPToolCatalogCache(capacity=2)
    first = cache.context(server())
    first.publish_if_newest(first.begin_fetch(), snapshot())
    cache.context(replace(server(), name="second"))
    assert cache.context(server()) is first
    cache.context(replace(server(), name="third"))
    cache.context(replace(server(), name="fourth"))
    assert first.current_tools_or() is not None
    assert cache.context(server()).current_tools_or() is None


def test_environment_uses_weak_object_identity_not_name():
    cache = MCPToolCatalogCache()
    transport = httpx.MockTransport(lambda _: httpx.Response(200))
    first = MCPHTTPEnvironment("remote", transport)
    second = MCPHTTPEnvironment("remote", transport)
    settings = server(environment_id="remote")
    entry = cache.context(settings, first)
    assert cache.context(settings, first) is entry
    assert cache.context(settings, second) is not entry
    reference = weakref.ref(first)
    del first
    gc.collect()
    assert reference() is None


@pytest.mark.parametrize(
    "changed",
    [
        {"name": "other"},
        {"url": "https://other.test"},
        {"environment_id": "remote"},
        {"http_headers": (("authorization", "secret"),)},
        {"bearer_token_env_var": "CACHE_TEST_TOKEN"},
    ],
)
def test_transport_identity_separates_connections(changed):
    cache = MCPToolCatalogCache()
    assert cache.context(server()) is not cache.context(replace(server(), **changed))


def test_view_policy_not_in_transport_identity():
    cache = MCPToolCatalogCache()
    settings = server()
    assert cache.context(settings) is cache.context(
        replace(
            settings,
            enabled_tools=("only",),
            required=True,
            timeout_seconds=1,
            default_tools_approval_mode="prompt",
            omit_tools_from=("direct",),
        )
    )
    assert cache.context(settings) is not cache.context(settings, agent_plugin=True)


@pytest.mark.parametrize("name", ["docs", "codex_apps"])
def test_named_credentials_missing_empty_changed_and_unrelated(monkeypatch, name):
    cache = MCPToolCatalogCache()
    settings = replace(server(bearer_token_env_var="CACHE_TEST_TOKEN"), name=name)
    monkeypatch.delenv("CACHE_TEST_TOKEN", raising=False)
    missing = cache.context(settings)
    monkeypatch.setenv("CACHE_TEST_UNRELATED", "changed")
    assert cache.context(settings) is missing
    monkeypatch.setenv("CACHE_TEST_TOKEN", "")
    empty = cache.context(settings)
    assert empty is not missing
    monkeypatch.setenv("CACHE_TEST_TOKEN", "sensitive")
    assert cache.context(settings) not in (empty, missing)
    assert "sensitive" not in repr(cache._entries)


def test_dynamic_credentials_bypass_and_stdio_cwd_is_not_canonicalized(tmp_path, monkeypatch):
    cache = MCPToolCatalogCache()
    assert cache.context(server(http_headers_helper="get-token")) is None
    remote = MCPServerSettings(
        "docs", "stdio", command="docs", env_vars=(MCPEnvVar("TOKEN", "remote"),)
    )
    assert cache.context(remote) is None
    settings = replace(remote, env_vars=())
    monkeypatch.chdir(tmp_path)
    first = cache.context(settings)
    directory = tmp_path / "actual"
    directory.mkdir()
    link = tmp_path / "alias"
    link.symlink_to(directory, target_is_directory=True)
    assert cache.context(replace(settings, cwd=link)) is not cache.context(
        replace(settings, cwd=directory)
    )
    monkeypatch.chdir(directory)
    assert cache.context(settings) is not first


@pytest.mark.parametrize(
    "value,expected",
    [(False, False), (True, True), (None, True), (0, True), ("false", True), ({}, True)],
)
def test_only_explicit_boolean_false_disables_cache(value, expected):
    initialized = validate_initialization(
        {
            "protocolVersion": "2025-06-18",
            "serverInfo": {"name": "docs", "version": "1"},
            "capabilities": {"experimental": {"codex/tool-catalog-cache": {"cacheable": value}}},
        }
    )
    assert initialized.tool_catalog_cacheable is expected
