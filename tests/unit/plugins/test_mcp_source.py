"""Ordinary host plugin MCP normalization follows the native source contract."""

import asyncio
import json

import pytest

from corki.plugins import PluginManager
from corki.tools import ToolRegistry


def load_plugin(root, servers, *, placement="inline", wrapped=True):
    manifest = root / ".codex-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    value = {"name": "sample"}
    if placement == "inline":
        value["mcpServers"] = servers
    else:
        filename = ".mcp.json" if placement == "default" else "servers.json"
        if placement == "explicit":
            value["mcpServers"] = "./" + filename
        (root / filename).write_text(json.dumps({"mcpServers": servers} if wrapped else servers))
    manifest.write_text(json.dumps(value))
    return PluginManager.discover_and_load(
        roots=(root,), disabled=frozenset(), registry=ToolRegistry()
    )


@pytest.mark.parametrize("cwd", [None, "scripts", "", "~/scripts", "../outside", "absolute"])
def test_stdio_cwd_is_only_rooted_when_explicit(tmp_path, cwd):
    root = tmp_path / "bundle"
    value = {"command": "fixture"}
    if cwd is not None:
        value["cwd"] = str(tmp_path / "absolute") if cwd == "absolute" else cwd
    expected = None if cwd is None else tmp_path / "absolute" if cwd == "absolute" else root / cwd
    manager = load_plugin(root, {"docs": value})
    try:
        assert len(manager.mcp_registrations) == 1
        assert manager.mcp_registrations[0].settings.cwd == expected
    finally:
        asyncio.run(manager.aclose())


def test_http_helper_keeps_runtime_cwd_unassigned_at_plugin_parse(tmp_path):
    manager = load_plugin(
        tmp_path / "bundle",
        {"docs": {"url": "https://fixture.invalid", "http_headers_helper": "./auth.sh"}},
    )
    try:
        assert manager.mcp_registrations[0].settings.cwd is None
    finally:
        asyncio.run(manager.aclose())


@pytest.mark.parametrize("placement", ["default", "explicit"])
@pytest.mark.parametrize("wrapped", [False, True])
def test_companion_file_supports_both_native_shapes(tmp_path, placement, wrapped):
    manager = load_plugin(
        tmp_path / "bundle",
        {"docs": {"command": "fixture"}},
        placement=placement,
        wrapped=wrapped,
    )
    try:
        assert [r.settings.name for r in manager.mcp_registrations] == ["docs"]
    finally:
        asyncio.run(manager.aclose())


@pytest.mark.parametrize("placement", ["inline", "default", "explicit"])
def test_invalid_mcp_sibling_does_not_discard_valid_server_or_plugin(tmp_path, placement):
    manager = load_plugin(
        tmp_path / "bundle",
        {"docs": {"command": "fixture"}, "bad": {"command": 42}},
        placement=placement,
    )
    try:
        assert [p.manifest.name for p in manager.plugins] == ["sample"]
        assert [r.settings.name for r in manager.mcp_registrations] == ["docs"]
        assert any("bad" in warning for warning in manager.warnings)
    finally:
        asyncio.run(manager.aclose())


@pytest.mark.parametrize("kind", ["http", "streamable_http", "streamable-http", "future-http"])
def test_plugin_type_annotation_does_not_replace_regular_transport_inference(tmp_path, kind):
    manager = load_plugin(
        tmp_path / "bundle", {"docs": {"type": kind, "url": "https://fixture.invalid"}}
    )
    try:
        assert len(manager.mcp_registrations) == 1
        assert manager.mcp_registrations[0].settings.transport == "http"
        assert bool(manager.warnings) is (kind == "future-http")
    finally:
        asyncio.run(manager.aclose())


@pytest.mark.parametrize("contents", ["[]", "null", "{bad"])
@pytest.mark.parametrize("placement", ["default", "explicit"])
def test_malformed_companion_only_disables_mcp_not_plugin(tmp_path, contents, placement):
    root = tmp_path / "bundle"
    initial = load_plugin(root, {"docs": {"command": "fixture"}}, placement=placement)
    asyncio.run(initial.aclose())
    (root / (".mcp.json" if placement == "default" else "servers.json")).write_text(contents)
    manager = PluginManager.discover_and_load(
        roots=(root,), disabled=frozenset(), registry=ToolRegistry()
    )
    try:
        assert [p.manifest.name for p in manager.plugins] == ["sample"]
        assert manager.mcp_registrations == ()
        assert manager.warnings
    finally:
        asyncio.run(manager.aclose())


@pytest.mark.parametrize("path", ["servers.json", "./", "../servers.json", "./../servers.json"])
def test_invalid_manifest_mcp_path_warns_and_uses_default_file(tmp_path, path):
    root = tmp_path / "bundle"
    initial = load_plugin(root, {"docs": {"command": "fixture"}}, placement="default")
    asyncio.run(initial.aclose())
    (root / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": "sample", "mcpServers": path})
    )
    manager = PluginManager.discover_and_load(
        roots=(root,), disabled=frozenset(), registry=ToolRegistry()
    )
    try:
        assert [r.settings.name for r in manager.mcp_registrations] == ["docs"]
        assert manager.warnings
    finally:
        asyncio.run(manager.aclose())


def test_ordinary_plugin_explicit_file_follows_symlink_like_default_file(tmp_path):
    root = tmp_path / "bundle"
    initial = load_plugin(root, {"old": {"command": "fixture"}}, placement="default")
    asyncio.run(initial.aclose())
    outside = tmp_path / "host-declared.json"
    outside.write_text(json.dumps({"docs": {"command": "fixture"}}))
    (root / "linked.json").symlink_to(outside)
    (root / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": "sample", "mcpServers": "./linked.json"})
    )
    manager = PluginManager.discover_and_load(
        roots=(root,), disabled=frozenset(), registry=ToolRegistry()
    )
    try:
        assert [r.settings.name for r in manager.mcp_registrations] == ["docs"]
        assert not manager.warnings
    finally:
        asyncio.run(manager.aclose())


def test_default_mcp_nonfile_is_not_opened(tmp_path, monkeypatch):
    root = tmp_path / "bundle"
    manifest = root / ".codex-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"name":"sample"}')
    (root / ".mcp.json").mkdir()
    original = type(root).read_text

    def read(path, *args, **kwargs):
        assert path != root / ".mcp.json", "non-file default must not be opened"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(type(root), "read_text", read)
    manager = PluginManager.discover_and_load(
        roots=(root,), disabled=frozenset(), registry=ToolRegistry()
    )
    try:
        assert [p.manifest.name for p in manager.plugins] == ["sample"]
        assert manager.mcp_registrations == () and not manager.warnings
    finally:
        asyncio.run(manager.aclose())
