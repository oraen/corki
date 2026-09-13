"""Source-mapped Agent Plugin contracts through the actual directory loader."""

import asyncio
import json
import os

import pytest

from corki.config.mcp_environment import MCPEnvVar
from corki.plugins import PluginManager
from corki.tools import ToolRegistry

SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"


def load(root, servers, *, metadata=None):
    root.mkdir(parents=True, exist_ok=True)
    (root / "plugin.json").write_text(
        json.dumps({"$schema": SCHEMA, "name": "sample", **(metadata or {})})
    )
    (root / "mcp.json").write_text(json.dumps({"$schema": MCP_SCHEMA, "mcpServers": servers}))
    return PluginManager.discover_and_load(
        roots=(root,), disabled=frozenset(), registry=ToolRegistry()
    )


def test_agent_fixed_components_and_optional_metadata_are_not_invented(tmp_path):
    root = tmp_path / "plugin"
    (root / "skills").mkdir(parents=True)
    manager = load(
        root, {"docs": {"type": "stdio", "command": "python"}}, metadata={"name": "acme.tools"}
    )
    try:
        assert len(manager.plugins) == 1
        manifest = manager.plugins[0].manifest
        assert (
            manifest.name == "acme.tools"
            and manifest.version is None
            and manifest.description is None
        )
        assert manifest.skills_path == root / "skills"
        registration = manager.mcp_registrations[0]
        assert registration.source.agent_plugin
        assert registration.settings.cwd == root
        assert dict(registration.settings.env) == {
            "PLUGIN_ROOT": str(root),
            "PLUGIN_DATA": str(root / ".plugin-data"),
        }
    finally:
        asyncio.run(manager.aclose())


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "stdio", "command": "../server"},
        {"type": "stdio", "command": "./../server"},
        {"type": "stdio", "command": "python", "cwd": "${PLUGIN_ROOT}/../outside"},
        {"type": "stdio", "command": "./scripts\\server"},
        {"type": "stdio", "command": "python", "cwd": None},
        {"type": "stdio", "command": "python", "future": True},
        {"type": "stdio", "command": "python", "env": {"PLUGIN_ROOT": "override"}},
        {"type": "streamable-http", "url": "http://example.test/mcp"},
        {"type": "streamable-http", "url": "https://user:pass@example.test/mcp"},
        {"type": "streamable-http", "url": "https://example.test/mcp#"},
        {"type": "streamable-http", "url": "https://example.test/mcp", "headers": None},
        {
            "type": "streamable-http",
            "url": "https://example.test/mcp",
            "headers": {"X-Test": "one", "x-test": "two"},
        },
        {
            "type": "streamable-http",
            "url": "https://example.test/mcp",
            "headers": {"Authorization": "bad\x00value"},
        },
        {"type": "sse", "url": "https://example.test/sse"},
    ],
)
def test_agent_invalid_server_does_not_remove_valid_sibling(tmp_path, bad):
    manager = load(
        tmp_path / "plugin", {"good": {"type": "stdio", "command": "python"}, "bad": bad}
    )
    try:
        assert len(manager.plugins) == 1
        assert [r.settings.name for r in manager.mcp_registrations] == ["good"]
        assert any("bad" in warning for warning in manager.warnings)
    finally:
        asyncio.run(manager.aclose())


def test_agent_valid_headers_are_filtered_after_validation(tmp_path):
    manager = load(
        tmp_path / "plugin",
        {
            "docs": {
                "type": "streamable-http",
                "url": "http://[::1]/mcp",
                "headers": {
                    "aUtHoRiZaTiOn": "not-a-credential",
                    "Host": "ignored",
                    "User-Agent": "ignored",
                    "Proxy-Authorization": "ignored",
                    "X-Plugin": "café",
                },
            }
        },
    )
    try:
        assert len(manager.mcp_registrations) == 1
        assert dict(manager.mcp_registrations[0].settings.http_headers) == {"X-Plugin": "café"}
    finally:
        asyncio.run(manager.aclose())


def test_agent_placeholders_are_single_pass_and_args_remain_opaque(tmp_path):
    root = tmp_path / "${PLUGIN_DATA}" / "plugin"
    manager = load(
        root,
        {
            "docs": {
                "type": "stdio",
                "command": "python",
                "args": ["${PLUGIN_ROOT}:${PLUGIN_DATA}", "${PLUGIN_ROOT}/../opaque"],
                "env": {"OPAQUE": "${PLUGIN_DATA}/../opaque"},
            }
        },
    )
    try:
        assert len(manager.mcp_registrations) == 1
        server = manager.mcp_registrations[0].settings
        assert server.args == (f"{root}:{root / '.plugin-data'}", f"{root}/../opaque")
        assert dict(server.env)["OPAQUE"] == f"{root / '.plugin-data'}/../opaque"
    finally:
        asyncio.run(manager.aclose())


@pytest.mark.parametrize("dangling", [False, True])
def test_agent_command_rejects_escaping_or_dangling_symlink_prefix(tmp_path, dangling):
    root = tmp_path / "plugin"
    root.mkdir()
    outside = tmp_path / "outside"
    if not dangling:
        outside.mkdir()
    (root / "link").symlink_to(outside)
    manager = load(
        root,
        {
            "good": {"type": "stdio", "command": "python"},
            "bad": {"type": "stdio", "command": "./link/missing"},
        },
    )
    try:
        assert len(manager.plugins) == 1
        assert [r.settings.name for r in manager.mcp_registrations] == ["good"]
        assert any("bad" in warning for warning in manager.warnings)
    finally:
        asyncio.run(manager.aclose())


@pytest.mark.parametrize(
    "metadata",
    [
        {"version": None},
        {"description": 3},
        {"author": {"name": None}},
        {"author": {"extra": "bad"}},
        {"keywords": None},
        {"name": "Bad_Name"},
        {"name": "double--dash"},
        {"extensions": {"com.openai": {"interface": 1}}},
    ],
)
def test_agent_invalid_metadata_cannot_load_legacy_overlay(tmp_path, metadata):
    root = tmp_path / "plugin"
    overlay = root / ".codex-plugin/plugin.json"
    overlay.parent.mkdir(parents=True)
    overlay.write_text(json.dumps({"name": "decoy"}))
    manager = load(root, {}, metadata=metadata)
    assert manager.plugins == ()
    assert manager.warnings


@pytest.mark.parametrize("inline", [False, True])
def test_agent_overlay_only_adds_local_references_to_existing_stdio(tmp_path, inline):
    root = tmp_path / "plugin"
    overlay = root / ".codex-plugin/plugin.json"
    overlay.parent.mkdir(parents=True)
    servers = {
        "docs": {
            "command": "ignored",
            "env_vars": [
                "TOKEN",
                {"name": "STATIC", "source": "local"},
                {"name": "REMOTE", "source": "remote"},
            ],
        },
        "extra": {"command": "not-added"},
        "http": {"url": "https://ignored.test", "http_headers": {"x-secret": "ignored"}},
    }
    overlay.write_text(json.dumps({"name": "decoy", **({"mcpServers": servers} if inline else {})}))
    if not inline:
        (root / ".mcp.json").write_text(json.dumps({"mcpServers": servers}))
    manager = load(
        root,
        {
            "docs": {
                "type": "stdio",
                "command": "portable",
                "env": {
                    "TOKEN": "${TOKEN}",
                    "STATIC": "literal",
                    "REMOTE": "${REMOTE}",
                    "ALIAS": "${TOKEN}",
                },
            },
            "http": {"type": "streamable-http", "url": "https://original.test"},
        },
        metadata={"extensions": {"com.openai": {"interface": {"displayName": "Original"}}}},
    )
    try:
        assert [p.manifest.name for p in manager.plugins] == ["sample"]
        registrations = {r.settings.name: r.settings for r in manager.mcp_registrations}
        assert set(registrations) == {"docs", "http"}
        docs = registrations["docs"]
        assert docs.command == "portable"
        assert docs.env_vars == ("TOKEN", MCPEnvVar("STATIC", "local"))
        assert dict(docs.env) == {
            "STATIC": "literal",
            "REMOTE": "${REMOTE}",
            "ALIAS": "${TOKEN}",
            "PLUGIN_ROOT": str(root),
            "PLUGIN_DATA": str(root / ".plugin-data"),
        }
        assert registrations["http"].url == "https://original.test"
        assert registrations["http"].http_headers is None
    finally:
        asyncio.run(manager.aclose())


@pytest.mark.parametrize("kind", ["missing", "symlink", "directory", "schema", "unknown_field"])
def test_agent_optional_mcp_failure_keeps_package_but_never_uses_legacy_mcp(tmp_path, kind):
    root = tmp_path / "plugin"
    root.mkdir()
    (root / "plugin.json").write_text(json.dumps({"$schema": SCHEMA, "name": "sample"}))
    (root / ".mcp.json").write_text(json.dumps({"decoy": {"command": "ignored"}}))
    source = root / "mcp.json"
    if kind == "directory":
        source.mkdir()
    elif kind == "symlink":
        source.symlink_to(root / ".mcp.json")
    elif kind in ("schema", "unknown_field"):
        source.write_text(
            json.dumps(
                {
                    "$schema": "unsupported" if kind == "schema" else MCP_SCHEMA,
                    "mcpServers": {"decoy": {"type": "stdio", "command": "ignored"}},
                    **({"unknown": True} if kind == "unknown_field" else {}),
                }
            )
        )
    manager = PluginManager.discover_and_load(
        roots=(root,), disabled=frozenset(), registry=ToolRegistry()
    )
    assert [p.manifest.name for p in manager.plugins] == ["sample"]
    assert manager.mcp_registrations == ()


@pytest.mark.parametrize("folder", [".codex-plugin", ".claude-plugin", ".cursor-plugin"])
def test_unrelated_root_json_allows_native_legacy_fallback(tmp_path, folder):
    (tmp_path / "plugin.json").write_text('{"name":"not-an-agent-plugin"}')
    legacy = tmp_path / folder / "plugin.json"
    legacy.parent.mkdir()
    legacy.write_text('{"name":"legacy"}')
    manager = PluginManager.discover_and_load(
        roots=(tmp_path,), disabled=frozenset(), registry=ToolRegistry()
    )
    assert [p.manifest.name for p in manager.plugins] == ["legacy"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX case-sensitive variable contract")
def test_agent_environment_reference_matching_is_case_sensitive_on_posix(tmp_path):
    root = tmp_path / "plugin"
    overlay = root / ".codex-plugin/plugin.json"
    overlay.parent.mkdir(parents=True)
    overlay.write_text(
        json.dumps({"mcpServers": {"docs": {"command": "ignored", "env_vars": ["TOKEN"]}}})
    )
    manager = load(
        root, {"docs": {"type": "stdio", "command": "portable", "env": {"token": "${TOKEN}"}}}
    )
    assert dict(manager.mcp_registrations[0].settings.env)["token"] == "${TOKEN}"


def test_agent_format_is_not_guessed_from_the_package_directory_name(tmp_path):
    manager = load(
        tmp_path / ".codex-plugin-data", {"mcpServers": {"type": "stdio", "command": "portable"}}
    )
    assert [p.manifest.name for p in manager.plugins] == ["sample"]
    assert manager.mcp_registrations[0].source.agent_plugin
    assert manager.mcp_registrations[0].settings.name == "mcpServers"


@pytest.mark.parametrize("field", ["$schema", "mcpServers"])
def test_agent_mcp_typed_envelope_rejects_duplicate_fields(tmp_path, field):
    root = tmp_path / "plugin"
    manager = load(root, {})
    asyncio.run(manager.aclose())
    pair = json.dumps(field) + ":" + json.dumps(MCP_SCHEMA if field == "$schema" else {})
    (root / "mcp.json").write_text(
        '{"$schema":'
        + json.dumps(MCP_SCHEMA)
        + ',"mcpServers":{"docs":{"type":"stdio","command":"python"}},'
        + pair
        + "}"
    )
    manager = PluginManager.discover_and_load(
        roots=(root,), disabled=frozenset(), registry=ToolRegistry()
    )
    assert len(manager.plugins) == 1
    assert manager.mcp_registrations == ()
    assert any("duplicate" in w for w in manager.warnings)


def test_agent_legacy_overlay_rejects_duplicate_typed_fields(tmp_path):
    root = tmp_path / "plugin"
    overlay = root / ".codex-plugin/plugin.json"
    overlay.parent.mkdir(parents=True)
    overlay.write_text('{"name":"first","name":"second"}')
    manager = load(root, {})
    assert manager.plugins == ()
    assert any("duplicate" in w for w in manager.warnings)


def test_agent_root_and_server_value_objects_keep_last_duplicate_value(tmp_path):
    root = tmp_path / "plugin"
    root.mkdir()
    (root / "plugin.json").write_text(
        '{"$schema":' + json.dumps(SCHEMA) + ',"name":"first","name":"second"}'
    )
    (root / "mcp.json").write_text(
        '{"$schema":'
        + json.dumps(MCP_SCHEMA)
        + ',"mcpServers":{"docs":{"type":"stdio","command":"first","command":"second"}}}'
    )
    manager = PluginManager.discover_and_load(
        roots=(root,), disabled=frozenset(), registry=ToolRegistry()
    )
    assert [p.manifest.name for p in manager.plugins] == ["second"]
    assert manager.mcp_registrations[0].settings.command == "second"


def test_agent_root_invalid_unicode_is_unrelated_and_allows_legacy_fallback(tmp_path):
    overlay = tmp_path / ".codex-plugin/plugin.json"
    overlay.parent.mkdir()
    overlay.write_text('{"name":"legacy"}')
    (tmp_path / "plugin.json").write_text(
        json.dumps({"$schema": SCHEMA, "name": "sample", "description": "\ud800"})
    )
    manager = PluginManager.discover_and_load(
        roots=(tmp_path,), disabled=frozenset(), registry=ToolRegistry()
    )
    assert [p.manifest.name for p in manager.plugins] == ["legacy"]


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "stdio", "command": "python", "args": ["\ud800"]},
        {"type": "stdio", "command": "python", "unknown": "\ud800"},
    ],
)
def test_agent_mcp_invalid_unicode_value_disables_the_file(tmp_path, bad):
    manager = load(
        tmp_path / "plugin", {"good": {"type": "stdio", "command": "python"}, "bad": bad}
    )
    assert len(manager.plugins) == 1
    assert manager.mcp_registrations == ()
    assert manager.warnings


@pytest.mark.parametrize("number", ["1e999", "1" * 5000], ids=["large-exponent", "long-integer"])
def test_agent_unknown_extension_numbers_do_not_invent_a_json_failure(tmp_path, number):
    (tmp_path / "plugin.json").write_text(
        '{"$schema":'
        + json.dumps(SCHEMA)
        + ',"name":"sample","extensions":{"com.openai":{"future":'
        + number
        + "}}}"
    )
    manager = PluginManager.discover_and_load(
        roots=(tmp_path,), disabled=frozenset(), registry=ToolRegistry()
    )
    assert [p.manifest.name for p in manager.plugins] == ["sample"]
