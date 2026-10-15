import asyncio
import json
import sys
from pathlib import Path

from corki.plugins import PluginManager
from corki.protocol.ids import ToolCallId
from corki.protocol.tools import ToolCall
from corki.skills import SkillService
from corki.tools import ToolContext, ToolExecutor, ToolRegistry


def test_codex_plugin_manifest_contributes_tools_skills_and_mcp(tmp_path: Path) -> None:
    root = tmp_path / "plugins" / "sample"
    manifest = root / ".codex-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "name": "sample",
                "version": "1.2.0",
                "description": "all capability types",
                "skills": "./skills",
                "entrypoint": "plugin.py:register",
                "mcpServers": {
                    "docs": {
                        "type": "http",
                        "url": "https://example.test/mcp",
                        "enabled_tools": ["read", "write"],
                        "disabled_tools": ["write"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "skills" / "lookup").mkdir(parents=True)
    (root / "skills" / "lookup" / "SKILL.md").write_text(
        "---\nname: lookup\ndescription: Look things up.\n---\nUse the tool.\n",
        encoding="utf-8",
    )
    (root / "plugin.py").write_text(
        "def register(api):\n"
        "    api.register_tool(name='echo', description='Echo text', "
        "parameters={'type': 'object'}, handler=lambda args, ctx: args)\n",
        encoding="utf-8",
    )
    registry = ToolRegistry()

    manager = PluginManager.discover_and_load(
        roots=(tmp_path / "plugins",), disabled=frozenset(), registry=registry
    )

    assert len(manager.plugins) == 1
    assert manager.plugins[0].tool_names == ("plugin__sample__echo",)
    assert manager.mcp_servers[0].name == "sample__docs"
    assert manager.mcp_servers[0].enabled_tools == ("read", "write")
    assert manager.mcp_servers[0].disabled_tools == ("write",)
    service = SkillService(
        home=tmp_path / ".corki",
        project_root=tmp_path,
        plugin_roots=manager.skill_roots,
        bundled_enabled=False,
    )
    assert service.snapshot(tmp_path).resolve("sample:lookup") is not None

    async def execute_plugin_tool() -> str:
        executor = ToolExecutor(registry, output_char_budget=1_000)
        result = await executor.execute(
            ToolCall(ToolCallId("plugin-1"), "plugin__sample__echo", {"value": "ok"}),
            ToolContext(cwd=tmp_path),
        )
        await manager.aclose()
        return result.content

    assert json.loads(asyncio.run(execute_plugin_tool())) == {"value": "ok"}


def test_disabled_plugin_is_not_loaded(tmp_path: Path) -> None:
    root = tmp_path / "plugins" / "sample" / ".codex-plugin"
    root.mkdir(parents=True)
    (root / "plugin.json").write_text(
        '{"name":"sample","description":"disabled"}', encoding="utf-8"
    )

    manager = PluginManager.discover_and_load(
        roots=(tmp_path / "plugins",),
        disabled=frozenset({"sample"}),
        registry=ToolRegistry(),
    )

    assert manager.plugins == ()


def test_broken_plugin_is_isolated_and_partial_tools_are_rolled_back(tmp_path: Path) -> None:
    root = tmp_path / "plugins" / "broken"
    manifest = root / ".corki-plugin" / "plugin.toml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "[plugin]\nname='broken'\nentrypoint='plugin.py:register'\n",
        encoding="utf-8",
    )
    (root / "plugin.py").write_text(
        "def register(api):\n"
        "    api.register_tool(name='partial', description='partial', "
        "parameters={'type': 'object'}, handler=lambda args, ctx: 'bad')\n"
        "    raise RuntimeError('boom')\n",
        encoding="utf-8",
    )
    registry = ToolRegistry()
    modules_before = {name for name in sys.modules if name.startswith("corki_plugin_broken_")}

    manager = PluginManager.discover_and_load(
        roots=(tmp_path / "plugins",), disabled=frozenset(), registry=registry
    )

    assert manager.plugins == ()
    assert registry.get("plugin__broken__partial") is None
    assert "boom" in manager.warnings[0]
    assert {
        name for name in sys.modules if name.startswith("corki_plugin_broken_")
    } == modules_before


def test_two_plugin_managers_own_distinct_module_lifecycles(tmp_path: Path) -> None:
    root = tmp_path / "plugins" / "sample"
    manifest = root / ".corki-plugin" / "plugin.toml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "[plugin]\nname='sample'\nentrypoint='plugin.py:register'\n",
        encoding="utf-8",
    )
    (root / "plugin.py").write_text("def register(api):\n    pass\n", encoding="utf-8")
    managers = [
        PluginManager.discover_and_load(
            roots=(tmp_path / "plugins",),
            disabled=frozenset(),
            registry=ToolRegistry(),
        )
        for _ in range(2)
    ]
    first_name = managers[0]._modules[0].__name__
    second_name = managers[1]._modules[0].__name__

    assert first_name != second_name
    assert first_name in sys.modules and second_name in sys.modules

    async def close() -> None:
        await managers[0].aclose()
        await managers[0].aclose()
        assert second_name in sys.modules
        await managers[1].aclose()

    asyncio.run(close())
    assert first_name not in sys.modules
    assert second_name not in sys.modules
