"""Generic plugin guidance survives catalog changes without stale capabilities."""

import asyncio

import pytest

from corki.config import CorkiSettings
from corki.context import active_history
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.plugins.context import PluginContextContributor
from corki.plugins.models import LoadedPlugin, PluginManifest
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    ContextItem,
    ContextRole,
    new_step_id,
)


@pytest.mark.parametrize("compact_available", [None, False, True])
def test_plugin_guidance_is_independent_of_catalog_changes_and_cold_resume(
    tmp_path, compact_available
):
    async def scenario():
        requests = []
        plugins = ()

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

        async def create(thread=None):
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(tmp_path, skills_enabled=False, plugins_enabled=False),
                home_path=tmp_path / "home",
                database_path=tmp_path / "state.db",
                thread_id=thread,
                context_contributors=(Source(),),
                model=Model(),
            )

        runtime = await create()
        try:
            for index, description in enumerate(("FIRST_CAPABILITY", "NEW_CAPABILITY", None)):
                plugins = (
                    (LoadedPlugin(PluginManifest("fixture", None, description, tmp_path)),)
                    if description is not None
                    else ()
                )
                assert isinstance([e async for e in runtime.stream(str(index))][-1], TurnCompleted)
                contexts = [i for i in requests[-1].items if isinstance(i, ContextItem)]
                guidance = [i for i in contexts if i.content_kind == "plugins.usage_instructions"]
                assert len(guidance) == 1
                assert "Plugins are not invoked directly" in guidance[0].content
                assert not any("CAPABILITY" in i.content for i in guidance)
                assert not any(
                    "extensions.plugins.guidance" in i.content and "no longer apply" in i.content
                    for i in contexts
                )
                assert all(i.content for i in contexts)
                catalog = [i for i in contexts if i.key == "extensions.plugins.catalog"]
                if description is None:
                    assert "no longer apply" in catalog[-1].content
                else:
                    assert description in catalog[-1].content
            original = await runtime._repository.load_items(runtime.thread_id)
            if compact_available is not None:
                if compact_available:
                    plugins = (
                        LoadedPlugin(PluginManifest("fixture", None, "DURING_COMPACT", tmp_path)),
                    )
                assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
                assert len(requests) == 4
                assert requests[-1].tools == ()
                compacted = await runtime._repository.load_items(runtime.thread_id)
                assert compacted[: len(original)] == original
                assert sum(isinstance(i, CompactionItem) for i in compacted) == 1
                assert not any(isinstance(i, ContextItem) for i in active_history(compacted))
                # Standalone compaction defers world-state rebuilding to normal prepare.
                assert isinstance(
                    [e async for e in runtime.stream("after compact")][-1], TurnCompleted
                )
                assert sum(
                    isinstance(i, ContextItem) and i.content_kind == "plugins.usage_instructions"
                    for i in requests[-1].items
                ) == int(compact_available)
                original = await runtime._repository.load_items(runtime.thread_id)
                assert original[: len(compacted)] == compacted
        finally:
            await runtime.aclose()

        plugins = (LoadedPlugin(PluginManifest("fixture", None, "RETURNED", tmp_path)),)
        cold = await create(runtime.thread_id)
        try:
            assert [e async for e in cold.resume_pending()] == []
            assert len(requests) == 3 + 2 * int(compact_available is not None)
            assert isinstance([e async for e in cold.stream("cold")][-1], TurnCompleted)
            assert (
                sum(
                    isinstance(i, ContextItem) and i.content_kind == "plugins.usage_instructions"
                    for i in requests[-1].items
                )
                == 1
            )
            history = await cold._repository.load_items(cold.thread_id)
            assert history[: len(original)] == original
            assert sum(
                isinstance(i, ContextItem)
                and i.content_kind == "plugins.usage_instructions"
                and bool(i.content)
                for i in history
            ) == (1 if compact_available is None else 2)
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("revoked", [False, True])
def test_legacy_mixed_catalog_migration_preserves_guidance_authority(tmp_path, revoked):
    async def scenario():
        requests = []
        plugins = (LoadedPlugin(PluginManifest("fixture", None, "CURRENT", tmp_path)),)

        class Source:
            def contributions(self, **kwargs):
                return PluginContextContributor(plugins).contributions(**kwargs)

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(tmp_path, skills_enabled=False, plugins_enabled=False),
            home_path=tmp_path / "home",
            database_path=tmp_path / "state.db",
            context_contributors=(Source(),),
            model=Model(),
        )
        legacy = ContextItem(
            "extensions.plugins.catalog",
            ContextRole.DEVELOPER,
            "<plugins_instructions>\n## Plugins\n"
            "- Plugins are not invoked directly. "
            "Use the concrete capability that matches the task.\n"
            "- fixture: RETIRED_CAPABILITY\n</plugins_instructions>",
            new_turn_id(),
            content_kind="corki.plugins.catalog",
        )
        old = (legacy,)
        if revoked:
            old += (
                ContextItem(
                    legacy.key,
                    legacy.role,
                    "The previously provided instructions and facts for "
                    "'extensions.plugins.catalog' no longer apply.",
                    legacy.turn_id,
                    snapshot_content="",
                    content_kind=legacy.content_kind,
                ),
            )
        try:
            await runtime._ensure_ready()
            await runtime._repository.append_items(runtime.thread_id, old)
            for text in ("migrate", "unchanged"):
                assert isinstance([e async for e in runtime.stream(text)][-1], TurnCompleted)
                contexts = [i for i in requests[-1].items if isinstance(i, ContextItem)]
                new_guidance = [
                    i for i in contexts if i.content_kind == "plugins.usage_instructions"
                ]
                assert len(new_guidance) == int(revoked)
                catalog = [i for i in contexts if i.key == legacy.key]
                assert "CURRENT" in catalog[-1].content
                if not revoked:
                    assert "General plugin usage guidance remains unchanged" in catalog[-1].content
                history = await runtime._repository.load_items(runtime.thread_id)
                assert history[: len(old)] == old
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
