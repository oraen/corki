"""Full policy resources, conditional bindings and owned startup seeding."""

import asyncio
import threading
from hashlib import sha256

import pytest

from corki.config import CorkiSettings
from corki.memory import LongTermMemoryService, SQLiteMemoryRepository
from corki.memory.consolidation_prompt import (
    build_consolidation_prompt,
    seed_extension_instructions,
)
from corki.prompting import PromptStore
from corki.protocol.ids import new_thread_id
from corki.storage import SQLiteSessionRepository


@pytest.mark.parametrize("exists", [False, True])
def test_extensions_follow_actual_worker_directory_presence(tmp_path, exists):
    if exists:
        (tmp_path / "extensions").mkdir()
    prompt = build_consolidation_prompt(PromptStore(), tmp_path)
    assert ("Memory extensions (under " in prompt) is exists
    assert ("Optional source-specific inputs:" in prompt) is exists
    assert str(tmp_path) in prompt and "#{" not in prompt


def test_full_policy_resource_matches_documented_native_adaptation():
    source = PromptStore().load("memory/consolidation").source
    assert sha256(source.encode()).hexdigest() == (
        "166f5f2f012074f48a6e292f4a87bcb7f26417031f79903ec05a88107ac2702d"
    )
    assert len(source.splitlines()) == 880


def test_custom_store_and_substitution_values_are_not_reparsed(tmp_path):
    prompts = tmp_path / "prompts/memory"
    prompts.mkdir(parents=True)
    (prompts / "consolidation.md").write_text("CUSTOM #{memory_root}")
    (prompts / "consolidation_runtime.md").write_text("CUSTOM RUNTIME")
    root = tmp_path / "#{not_a_placeholder}"
    root.mkdir()
    value = build_consolidation_prompt(PromptStore(root=prompts.parent), root)
    assert value == f"CUSTOM {root}\nCUSTOM RUNTIME"


@pytest.mark.parametrize("custom", [False, True])
def test_seeded_instructions_never_replace_user_content(tmp_path, custom):
    path = tmp_path / "extensions/ad_hoc/instructions.md"
    if custom:
        path.parent.mkdir(parents=True)
        path.write_text("CUSTOM INTERPRETATION\n")
    seed_extension_instructions(tmp_path)
    expected = (
        "CUSTOM INTERPRETATION\n" if custom else PromptStore().render("memory/ad_hoc_instructions")
    )
    assert path.read_text() == expected
    seed_extension_instructions(tmp_path)
    assert path.read_text() == expected


def test_seed_rejects_redirect_without_touching_target(tmp_path):
    target = tmp_path / "outside"
    target.write_text("KEEP")
    path = tmp_path / "extensions/ad_hoc/instructions.md"
    path.parent.mkdir(parents=True)
    path.symlink_to(target)
    with pytest.raises(ValueError, match="regular file"):
        seed_extension_instructions(tmp_path)
    assert target.read_text() == "KEEP"


def test_cancelled_startup_joins_seed_writer_before_returning(tmp_path, monkeypatch):
    async def scenario():
        from corki.memory import pipeline

        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        original = pipeline.seed_extension_instructions

        def held(root):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
            original(root)

        class Model:
            async def stream(self, request):
                raise AssertionError("cancelled before any model request")
                yield

            async def aclose(self):
                pass

        sessions = SQLiteSessionRepository(tmp_path / "sessions.db")
        service = LongTermMemoryService(
            settings=CorkiSettings(tmp_path, skills_enabled=False, memories_enabled=True),
            repository=SQLiteMemoryRepository(tmp_path / "sessions.db"),
            model=Model(),
            root=tmp_path / "memories",
        )
        monkeypatch.setattr(pipeline, "seed_extension_instructions", held)
        task = asyncio.create_task(service.run_once(new_thread_id()))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            await service.aclose()
            await sessions.close()
        assert (tmp_path / "memories/extensions/ad_hoc/instructions.md").is_file()

    asyncio.run(scenario())
