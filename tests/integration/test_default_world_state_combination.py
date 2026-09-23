"""Default context producers share one ordered model request and cold history."""

import asyncio

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.memory.context import MemoryContextContributor
from corki.models import ModelCompleted
from corki.plugins.context import PluginContextContributor
from corki.plugins.models import LoadedPlugin, PluginManifest
from corki.protocol.events import TurnCompleted
from corki.protocol.items import AssistantMessageItem, ContextItem, ContextRole, new_step_id
from corki.protocol.tools import ToolExposure, ToolResult, ToolSpec
from corki.tools import ToolRegistry


def test_plugin_and_deferred_catalog_deltas_share_step_order_and_cold_baseline(tmp_path):
    async def scenario():
        plugins = (LoadedPlugin(PluginManifest("fixture", None, "FIRST_PLUGIN", tmp_path)),)
        requests = []

        class Source:
            def contributions(self, **kwargs):
                return PluginContextContributor(plugins).contributions(**kwargs)

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class Deferred:
            def __init__(self, description):
                self.spec = ToolSpec(
                    "docs::read",
                    "Read a document",
                    {"type": "object"},
                    exposure=ToolExposure.DEFERRED,
                    namespace_description=description,
                )

            async def execute(self, call, context):
                return ToolResult(call.id, call.name, "read")

        registry = ToolRegistry()
        owner = registry.create_owner()
        registry.replace_owned(owner, (Deferred("FIRST_DOCS"),))
        settings = CorkiSettings(
            working_directory=tmp_path,
            plugins_enabled=False,
            skills_enabled=False,
            deferred_tool_world_state=True,
        )

        async def create(thread=None):
            return await LangGraphRuntime.acreate(
                settings=settings,
                database_path=tmp_path / "history.db",
                home_path=tmp_path / "home",
                model=Model(),
                registry=registry,
                context_contributors=(Source(),),
                thread_id=thread,
            )

        runtime = await create()
        try:
            assert isinstance([e async for e in runtime.stream("first")][-1], TurnCompleted)
            first = await runtime._repository.load_items(runtime.thread_id)
            plugins = ()
            registry.replace_owned(owner, ())
            assert isinstance([e async for e in runtime.stream("remove")][-1], TurnCompleted)
            second = await runtime._repository.load_items(runtime.thread_id)
            removed = [i for i in second[len(first) :] if isinstance(i, ContextItem)]
            assert [i.key for i in removed if i.content] == [
                "tools.deferred_namespaces",
                "extensions.plugins.catalog",
            ]
            assert "Removed deferred tool namespaces" in removed[0].content
            assert "no longer apply" in removed[1].content
            plugins = (LoadedPlugin(PluginManifest("fixture", None, "RETURNED_PLUGIN", tmp_path)),)
            registry.replace_owned(owner, (Deferred("RETURNED_DOCS"),))
            assert isinstance([e async for e in runtime.stream("return")][-1], TurnCompleted)
            third = await runtime._repository.load_items(runtime.thread_id)
            restored = [i for i in third[len(second) :] if isinstance(i, ContextItem)]
            assert [i.key for i in restored if i.content] == [
                "tools.deferred_namespaces",
                "extensions.plugins.catalog",
            ]
            assert not any(i.key == "extensions.plugins.guidance" for i in restored)
            assert "RETURNED_DOCS" in restored[0].content
            assert "RETURNED_PLUGIN" in restored[1].content
            thread = runtime.thread_id
        finally:
            await runtime.aclose()

        registry = ToolRegistry()
        registry.register(Deferred("RETURNED_DOCS"))
        cold = await create(thread)
        try:
            assert isinstance([e async for e in cold.stream("unchanged")][-1], TurnCompleted)
            after = await cold._repository.load_items(thread)
            assert after[: len(third)] == third
            assert not any(isinstance(i, ContextItem) for i in after[len(third) :]), (
                "unchanged state should not append new context facts"
            )
            keys = [i.key for i in requests[-1].items if isinstance(i, ContextItem)]
            assert keys.count("extensions.plugins.guidance") == 1
            assert keys.count("tools.deferred_namespaces") == 3
        finally:
            await cold.aclose()

    asyncio.run(scenario())


def test_default_sections_share_ordered_request_and_survive_cold_turn(tmp_path):
    async def scenario():
        home = tmp_path / "home"
        skill = home / "skills" / "fixture" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: fixture\ndescription: Fixture skill\n---\nBODY\n")
        memory_root = tmp_path / "memories"
        memory_root.mkdir()
        (memory_root / "memory_summary.md").write_text("MEMORY_INDEX")
        (tmp_path / "AGENTS.md").write_text("PROJECT_RULE")
        plugin = LoadedPlugin(PluginManifest("fixture", None, "LOCAL_PLUGIN", tmp_path))
        contributors = (
            MemoryContextContributor(memory_root, enabled=True, token_limit=500),
            PluginContextContributor((plugin,)),
        )
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class Deferred:
            spec = ToolSpec(
                "docs::read",
                "Read a document",
                {"type": "object"},
                exposure=ToolExposure.DEFERRED,
                namespace_description="Documentation",
            )

            async def execute(self, call, context):
                return ToolResult(call.id, call.name, "read")

        settings = CorkiSettings(
            working_directory=tmp_path,
            plugins_enabled=False,
            memories_enabled=False,
            collaboration_mode="plan",
            deferred_tool_world_state=True,
        )

        async def create(thread=None):
            registry = ToolRegistry()
            registry.register(Deferred())
            return await LangGraphRuntime.acreate(
                settings=settings,
                database_path=tmp_path / "history.db",
                home_path=home,
                model=Model(),
                registry=registry,
                context_contributors=contributors,
                thread_id=thread,
            )

        warm = await create()
        try:
            assert isinstance([event async for event in warm.stream("Start")][-1], TurnCompleted)
            keys = [item.key for item in requests[0].items if isinstance(item, ContextItem)]
            developer_order = (
                "memory.instructions",
                "extensions.skills.catalog",
                "permissions",
                "mode.plan",
                "extensions.plugins.guidance",
                "tools.deferred_namespaces",
                "extensions.plugins.catalog",
            )
            user_order = ("project.agents", "environment.primary", "project.snapshot")
            ordered = (*developer_order, *user_order)
            assert all(key in keys for key in ordered), (keys, set(ordered) - set(keys))
            assert [keys.index(key) for key in developer_order] == sorted(
                keys.index(key) for key in developer_order
            ), " | ".join(keys)
            assert [keys.index(key) for key in user_order] == sorted(
                keys.index(key) for key in user_order
            ), " | ".join(keys)
            context = [item for item in requests[0].items if isinstance(item, ContextItem)]
            assert all(
                item.role is ContextRole.DEVELOPER for item in context[: len(developer_order)]
            )
            assert all(item.role is ContextRole.USER for item in context[len(developer_order) :])
            before = await warm._repository.load_items(warm.thread_id)
            thread = warm.thread_id
        finally:
            await warm.aclose()

        cold = await create(thread)
        try:
            assert isinstance([event async for event in cold.stream("Continue")][-1], TurnCompleted)
            after = await cold._repository.load_items(thread)
            assert after[: len(before)] == before
            second_keys = [item.key for item in requests[1].items if isinstance(item, ContextItem)]
            assert second_keys.count("memory.instructions") == 1
            assert second_keys.count("tools.deferred_namespaces") == 1
        finally:
            await cold.aclose()

    asyncio.run(scenario())
