"""Inventory identities derive from selected surfaces and captured dispatch ownership."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from corki.config import CorkiSettings
from corki.mcp.tools import MCPTool
from corki.protocol.tools import ToolSpec
from corki.tools import ToolRegistry


def test_mcp_dispatch_owner_is_captured_atomically_and_not_search_label():
    registry = ToolRegistry()
    owner = registry.create_owner()

    async def unexpected_call(*args, **kwargs):
        raise AssertionError("Inventory inspection must not execute a tool")

    tool = MCPTool(
        "trusted-owner",
        {"name": "read", "inputSchema": {}},
        None,
        call_router=unexpected_call,
    )
    tool._spec = replace(tool.spec, source="fake-owner")
    registry.replace_owned(owner, (tool,))
    registry.seal()
    before = registry.snapshot()
    tool._server_name = "mutated"
    assert before.mcp_server_name(tool.spec.name) == "trusted-owner"
    replacement = SimpleNamespace(spec=tool.spec)
    registry.replace_owned(owner, (replacement,))
    after = registry.snapshot()
    assert after.mcp_server_name(tool.spec.name) is None
    bad = SimpleNamespace(spec=ToolSpec("bad", "bad", {}), mcp_server_name=42)
    with pytest.raises(ValueError, match="dispatcher owner"):
        registry.replace_owned(owner, (bad,))
    assert registry.snapshot().specs() == after.specs()


@pytest.mark.parametrize("bad", [None, "true", 1, {}, []])
def test_legacy_inventory_configuration_is_inert(tmp_path, bad):
    settings = CorkiSettings(
        tmp_path, execution_permissions=None, turn_metadata_includes_tool_info=bad
    )
    assert settings.turn_metadata_includes_tool_info is False


def test_inventory_table_type_is_checked_without_requiring_the_feature(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("[features]\ntool_registry=true\n")
    with pytest.raises(ValueError, match="tool_registry must be a TOML table"):
        CorkiSettings.for_directory(tmp_path, config_file=config)


def test_empty_host_owner_is_not_confused_with_absent_owner():
    registry = ToolRegistry()
    registry.register(SimpleNamespace(spec=ToolSpec("read", "read", {}), mcp_server_name=""))
    snapshot = registry.snapshot()
    assert snapshot.mcp_server_name("read") == ""


@pytest.mark.parametrize("old_inventory", [None, '{"ns":{"functions":{"secret":{}}}}', "invalid"])
def test_disabled_inventory_does_not_request_remote_identity(old_inventory):
    from corki.context.window import ContextWindowManager

    def unused_identity():
        raise AssertionError("disabled inventory must not acquire remote identity")

    window = ContextWindowManager(
        repository=None,
        model=None,
        model_name="test",
        context_window_tokens=1000,
        remote_identity=unused_identity,
    )
    assert (
        asyncio.run(window.client_metadata("thread", "turn", tool_inventory_json=old_inventory))
        is None
    )
