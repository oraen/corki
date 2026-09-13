"""Real skill and memory producers keep distinct ordering and history ownership."""

import asyncio

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.memory.context import MemoryContextContributor
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.items import AssistantMessageItem, ContextItem, UserMessageItem, new_step_id


@pytest.mark.parametrize("full_permissions", [False, True])
def test_memory_skill_order_and_catalog_removal_preserve_selected_input(tmp_path, full_permissions):
    async def scenario():
        home = tmp_path / "home"
        skill = home / "skills" / "fixture" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: fixture\ndescription: Fixture skill\n---\nSELECTED_BODY\n")
        memory = tmp_path / "memories"
        memory.mkdir()
        summary = memory / "memory_summary.md"
        summary.write_text("MEMORY_INDEX")
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                plugins_enabled=False,
                memories_enabled=False,
                include_permissions_instructions=full_permissions,
            ),
            database_path=tmp_path / "history.db",
            home_path=home,
            model=Model(),
            context_contributors=(MemoryContextContributor(memory, enabled=True, token_limit=500),),
        )
        try:
            assert isinstance([e async for e in runtime.stream("Use $fixture")][-1], TurnCompleted)
            initial = requests[0].items
            contexts = [i for i in initial if isinstance(i, ContextItem)]
            keys = [i.key for i in contexts]
            assert keys.index("memory.instructions") < keys.index("extensions.skills.catalog")
            if full_permissions:
                assert keys.index("extensions.skills.catalog") < keys.index("permissions")
            else:
                assert keys.index("mode.default") < keys.index("extensions.skills.catalog")
            selected = [i for i in contexts if i.source_input_id is not None]
            assert len(selected) == 1 and "SELECTED_BODY" in selected[0].content
            user = next(i for i in initial if isinstance(i, UserMessageItem))
            assert selected[0].source_input_id == user.id
            assert selected[0].role.value == "user"
            stored = await runtime._repository.load_items(runtime.thread_id)

            skill.unlink()
            summary.unlink()
            assert isinstance([e async for e in runtime.stream("Continue")][-1], TurnCompleted)
            after = await runtime._repository.load_items(runtime.thread_id)
            assert after[: len(stored)] == stored
            additions = [i for i in after[len(stored) :] if isinstance(i, ContextItem)]
            assert not any(i.key == "memory.instructions" for i in additions)
            assert any(i.key == "extensions.skills.catalog" for i in additions)
            assert not any(i.key == selected[0].key for i in additions)
            assert [
                i
                for i in requests[1].items
                if isinstance(i, ContextItem) and i.key == selected[0].key
            ] == selected
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("deleted", [False, True])
@pytest.mark.parametrize("initially_present", [False, True])
def test_memory_reads_only_new_window_including_cold_and_compaction(
    tmp_path, automatic, deleted, initially_present
):
    from corki.models.types import ModelUsage
    from corki.protocol.events import ContextCompacted

    async def scenario():
        root = tmp_path / "memories"
        root.mkdir()
        summary = root / "memory_summary.md"
        if initially_present:
            summary.write_text("OLD_INDEX")
        reads, requests = [], []

        class Source(MemoryContextContributor):
            def contributions(self, **kwargs):
                reads.append("read")
                return super().contributions(**kwargs)

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),),
                    ModelUsage(180_000 if len(requests) == 3 else 10, 10),
                )

            async def aclose(self):
                pass

        async def create(thread=None):
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    working_directory=tmp_path,
                    skills_enabled=False,
                    plugins_enabled=False,
                    memories_enabled=False,
                    context_window_tokens=200_000,
                    auto_compact_tokens=100_000 if automatic else 190_000,
                ),
                database_path=tmp_path / "history.db",
                home_path=tmp_path / "home",
                model=Model(),
                thread_id=thread,
                context_contributors=(Source(root, enabled=True, token_limit=500),),
            )

        def memory_items(request):
            return [
                i
                for i in request.items
                if isinstance(i, ContextItem) and i.key == "memory.instructions"
            ]

        runtime = await create()
        try:
            assert isinstance([e async for e in runtime.stream("first")][-1], TurnCompleted)
            original = memory_items(requests[-1])
            assert len(original) == int(initially_present)
            if initially_present:
                assert "OLD_INDEX" in original[0].content
            if deleted:
                summary.unlink(missing_ok=True)
            else:
                summary.write_text("NEW_INDEX")
            assert isinstance([e async for e in runtime.stream("same window")][-1], TurnCompleted)
            assert memory_items(requests[-1]) == original
            assert len(reads) == 1
            thread = runtime.thread_id
            await runtime.aclose()
            runtime = await create(thread)
            assert isinstance([e async for e in runtime.stream("cold window")][-1], TurnCompleted)
            assert memory_items(requests[-1]) == original
            assert len(reads) == 1
            prefix = await runtime._repository.load_items(thread)
            if not automatic:
                assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
                assert len(reads) == 1
            events = [e async for e in runtime.stream("new window")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            if automatic:
                assert any(isinstance(e, ContextCompacted) for e in events)
            assert len(reads) == 2
            current = memory_items(requests[-1])
            assert len(current) == (0 if deleted else 1)
            if not deleted:
                assert "NEW_INDEX" in current[0].content
                assert current[0].id not in {i.id for i in original}
            stored = await runtime._repository.load_items(thread)
            assert stored[: len(prefix)] == prefix
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
