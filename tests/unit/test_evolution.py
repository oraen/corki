"""Procedural learning contracts; all homes, skills and model calls are isolated."""

import asyncio
import json
import uuid

import pytest

from corki.config import CorkiSettings
from corki.evolution.state import EvolutionState
from corki.evolution.switch import EvolutionSwitch
from corki.evolution.tools import SkillManageTool
from corki.protocol.tools import ToolCall
from corki.skills.service import SkillService
from corki.tools.base import ToolContext

DOCUMENT = (
    "---\nname: example-workflow\ndescription: Verified example workflow.\n---\n\nRead first.\n"
)


def configure(home, enabled):
    home.mkdir(exist_ok=True, parents=True)
    (home / "settings.json").write_text(
        json.dumps(
            {
                "features": {"self_evolution": enabled},
                "memories": {"enabled": False},
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def tools(tmp_path):
    home, cwd = tmp_path / "home", tmp_path / "work"
    cwd.mkdir()
    configure(home, True)
    (home / "skills").mkdir()
    service = SkillService(
        home=home, project_root=cwd, compatibility_home=tmp_path / "user", bundled_enabled=False
    )
    tool = SkillManageTool(home, service, cwd, EvolutionSwitch(home))
    return home, cwd, service, tool


async def invoke(tool, cwd, **args):
    result = await tool.execute(ToolCall(str(uuid.uuid4()), "skill_manage", args), ToolContext(cwd))
    return result, None if result.is_error else json.loads(result.content)


def test_switch_is_opt_in_and_changes_without_reloading_model_settings(tmp_path):
    configure(tmp_path, False)
    switch = EvolutionSwitch(tmp_path)
    assert not switch.enabled()
    configure(tmp_path, True)
    assert switch.enabled()
    (tmp_path / "settings.json").write_text("{broken")
    assert not switch.enabled()
    configure(tmp_path, False)
    assert not switch.enabled()
    assert not (tmp_path / "evolution").exists()


def test_admitted_write_fence_does_not_poll_the_switch(tmp_path, monkeypatch):
    from corki.evolution.tools import WriteFence

    configure(tmp_path, True)
    switch = EvolutionSwitch(tmp_path)
    fence = WriteFence(switch)
    configure(tmp_path, False)

    def unexpected_read():
        pytest.fail("Existing work must not poll the evolution switch")

    monkeypatch.setattr(switch, "enabled", unexpected_read)
    for _ in range(100):
        fence.check()
    fence.revoke()
    with pytest.raises(ValueError, match="取消"):
        fence.check()


@pytest.mark.parametrize("value", [None, 1, "true", {}, []])
def test_self_evolution_requires_a_real_boolean(tmp_path, value):
    configure(tmp_path, value)
    with pytest.raises(ValueError, match="self_evolution"):
        CorkiSettings.for_directory(tmp_path, config_file=tmp_path / "settings.json")


def test_versioned_read_patch_restore_and_disabled_retention(tools):
    home, cwd, service, tool = tools

    async def check():
        result, created = await invoke(
            tool, cwd, action="create", name="example-workflow", content=DOCUMENT
        )
        assert not result.is_error and created["changed"]
        path = home / "skills/example-workflow/SKILL.md"
        denied, _ = await invoke(
            tool,
            cwd,
            action="patch",
            name="example-workflow",
            old_text="Read first.",
            new_text="Read and verify first.",
        )
        assert denied.is_error and path.read_text() == DOCUMENT
        _, read = await invoke(tool, cwd, action="read", name="example-workflow")
        result, edit = await invoke(
            tool,
            cwd,
            action="patch",
            name="example-workflow",
            old_text="Read first.",
            new_text="Read and verify first.",
            revision=read["revision"],
        )
        assert not result.is_error and "verify" in path.read_text()
        _, history = await invoke(tool, cwd, action="history", name="example-workflow")
        assert history["versions"][0]["id"] == edit["version"]
        _, read = await invoke(tool, cwd, action="read", name="example-workflow")
        result, _ = await invoke(
            tool,
            cwd,
            action="restore",
            name="example-workflow",
            version=edit["version"],
            revision=read["revision"],
        )
        assert not result.is_error and path.read_text() == DOCUMENT
        configure(home, False)
        # A newly admitted tool sees the switch; existing work retains consent.
        tool.fence.check()
        disabled_tool = SkillManageTool(home, service, cwd, EvolutionSwitch(home))
        result, _ = await invoke(
            disabled_tool, cwd, action="create", name="another", content=DOCUMENT
        )
        assert result.is_error
        # Disabling learning never disables/deletes what was already learned.
        skill = service.snapshot(cwd).resolve("example-workflow")
        assert skill and service.read(skill) == DOCUMENT
        assert json.loads((home / "settings.json").read_text())["memories"]["enabled"] is False

    asyncio.run(check())


def test_stale_revision_and_invalid_frontmatter_never_overwrite(tools):
    home, cwd, _, tool = tools

    async def check():
        await invoke(tool, cwd, action="create", name="example-workflow", content=DOCUMENT)
        path = home / "skills/example-workflow/SKILL.md"
        _, read = await invoke(tool, cwd, action="read", name="example-workflow")
        path.write_text(DOCUMENT + "External edit.\n")
        denied, _ = await invoke(
            tool,
            cwd,
            action="write_file",
            name="example-workflow",
            content=DOCUMENT,
            revision=read["revision"],
        )
        assert denied.is_error and "External edit." in path.read_text()
        _, read = await invoke(tool, cwd, action="read", name="example-workflow")
        denied, _ = await invoke(
            tool,
            cwd,
            action="write_file",
            name="example-workflow",
            content=DOCUMENT.replace("name: example-workflow", "name: renamed"),
            revision=read["revision"],
        )
        assert denied.is_error and "External edit." in path.read_text()

    asyncio.run(check())


@pytest.mark.parametrize(
    "file",
    ["../settings.json", "/tmp/escape", "a\\b", "C:/escape", ".system/SKILL.md", "scripts/CON.py"],
)
def test_resource_boundaries_include_windows_paths(tools, file):
    _, cwd, _, tool = tools

    async def check():
        await invoke(tool, cwd, action="create", name="example-workflow", content=DOCUMENT)
        result, _ = await invoke(tool, cwd, action="read", name="example-workflow", file=file)
        assert result.is_error

    asyncio.run(check())


def test_shared_and_builtin_skills_are_not_mutated(tools):
    home, cwd, service, tool = tools
    service = SkillService(home=home, project_root=cwd, compatibility_home=home.parent / "user")
    tool = SkillManageTool(home, service, cwd, EvolutionSwitch(home))
    builtin = next(s for s in service.snapshot(cwd).skills if s.scope.value == "system")
    public = home.parent / "user/.agents/skills/shared"
    public.mkdir(parents=True)
    public.joinpath("SKILL.md").write_text(DOCUMENT.replace("example-workflow", "shared"))

    async def check():
        for name, path in ((builtin.name, builtin.path), ("shared", public / "SKILL.md")):
            original = path.read_bytes()
            _, read = await invoke(tool, cwd, action="read", name=name)
            assert read and not read["editable"]
            result, _ = await invoke(
                tool,
                cwd,
                action="write_file",
                name=name,
                content=DOCUMENT,
                revision=read["revision"],
            )
            assert result.is_error and path.read_bytes() == original
        assert service.snapshot(cwd).resolve("shared")

    asyncio.run(check())


def test_counters_are_idempotent_and_manual_authoring_suppresses_redundant_review(tmp_path):
    state = EvolutionState(tmp_path)
    for count in range(9):
        assert not state.completed_turn("t", str(count), 1)
    assert state.completed_turn("t", "9", 1)
    assert not state.completed_turn("t", "9", 1)
    assert not state.completed_turn("t", "10", 10, authored=True)
    assert not state.completed_turn("t", "11", 1)


def test_revoke_before_publication_blocks_late_worker_write(tools, monkeypatch):
    import threading

    _, cwd, _, tool = tools
    entered, release = threading.Event(), threading.Event()
    original = tool.state.prepare_edit

    def paused(*args):
        result = original(*args)
        entered.set()
        assert release.wait(3)
        return result

    monkeypatch.setattr(tool.state, "prepare_edit", paused)

    async def check():
        task = asyncio.create_task(
            invoke(tool, cwd, action="create", name="example-workflow", content=DOCUMENT)
        )
        assert await asyncio.to_thread(entered.wait, 3)
        tool.fence.revoke()
        release.set()
        result, _ = await task
        assert result.is_error
        assert not (tool.home / "skills/example-workflow/SKILL.md").exists()

    asyncio.run(check())


def test_disabling_an_individual_skill_blocks_stale_reads_and_duplicate_create(tools):
    home, cwd, _, tool = tools

    async def check():
        await invoke(tool, cwd, action="create", name="example-workflow", content=DOCUMENT)
        path = home / "skills/example-workflow/SKILL.md"
        _, read = await invoke(tool, cwd, action="read", name="example-workflow")
        config = json.loads((home / "settings.json").read_text())
        config["skills"] = {"config": [{"path": str(path), "enabled": False}]}
        (home / "settings.json").write_text(json.dumps(config))
        result, _ = await invoke(
            tool,
            cwd,
            action="write_file",
            name="example-workflow",
            content=DOCUMENT + "Changed.",
            revision=read["revision"],
        )
        assert result.is_error and path.read_text() == DOCUMENT
        result, _ = await invoke(
            tool, cwd, action="create", name="example-workflow", content=DOCUMENT
        )
        assert result.is_error

    asyncio.run(check())


def test_new_support_files_require_read_and_symlinks_are_rejected(tools, tmp_path):
    home, cwd, _, tool = tools

    async def check():
        await invoke(tool, cwd, action="create", name="example-workflow", content=DOCUMENT)
        result, _ = await invoke(
            tool,
            cwd,
            action="write_file",
            name="example-workflow",
            file="references/checks.md",
            content="Check the result.",
        )
        assert result.is_error
        _, read = await invoke(
            tool, cwd, action="read", name="example-workflow", file="references/checks.md"
        )
        assert not read["exists"]
        result, _ = await invoke(
            tool,
            cwd,
            action="write_file",
            name="example-workflow",
            file="references/checks.md",
            content="Check the result.",
        )
        assert not result.is_error
        outside = tmp_path / "outside"
        outside.mkdir()
        (home / "skills/example-workflow/link").symlink_to(outside, target_is_directory=True)
        result, _ = await invoke(
            tool, cwd, action="read", name="example-workflow", file="link/checks.md"
        )
        assert result.is_error and not list(outside.iterdir())

    asyncio.run(check())


def test_foreign_edit_lock_is_not_released_by_contending_tool(tools):
    import hashlib

    from corki.storage.file_lock import open_lock, try_lock, unlock_and_close

    home, cwd, _, tool = tools
    locks = home / "evolution/locks"
    locks.mkdir(parents=True)
    directory = home / "skills/example-workflow"
    name = hashlib.sha256(str(directory).encode()).hexdigest() + ".lock"
    owner = open_lock(locks / name)
    assert try_lock(owner)

    async def check():
        result, _ = await invoke(
            tool, cwd, action="create", name="example-workflow", content=DOCUMENT
        )
        assert result.is_error
        probe = open_lock(locks / name)
        try:
            assert not try_lock(probe)
        finally:
            probe.close()

    try:
        asyncio.run(check())
    finally:
        unlock_and_close(owner)


def test_versions_are_bounded_even_when_publication_never_finishes(tmp_path):
    state = EvolutionState(tmp_path)
    for index in range(25):
        state.prepare_edit("example", "SKILL.md", str(index), str(index + 1), "review", None)
    versions = state.history("example", "SKILL.md")
    assert len(versions) == 20
    assert all(row["status"] == "prepared" for row in versions)
    assert state.prior_content(versions[-1]["id"], "example", "SKILL.md") == "5"


def test_failed_create_never_removes_an_existing_directory(tools, monkeypatch):
    home, cwd, _, tool = tools
    directory = home / "skills/example-workflow"
    # Simulate another actor creating the directory after catalog validation.
    original = tool._write

    def raced(*args):
        directory.mkdir()
        return original(*args)

    monkeypatch.setattr(tool, "_write", raced)

    async def check():
        result, _ = await invoke(
            tool, cwd, action="create", name="example-workflow", content=DOCUMENT
        )
        assert result.is_error and directory.is_dir()
        assert not list(directory.iterdir())

    asyncio.run(check())


def test_invalid_yaml_is_a_tool_error_without_partial_files(tools):
    home, cwd, _, tool = tools

    async def check():
        result, _ = await invoke(
            tool, cwd, action="create", name="broken", content="---\nname: [\n---\nBody."
        )
        assert result.is_error
        assert not (home / "skills/broken").exists()

    asyncio.run(check())


def test_skill_patch_preserves_existing_file_permissions(tools):
    import os
    import stat

    home, cwd, _, tool = tools

    async def check():
        await invoke(tool, cwd, action="create", name="example-workflow", content=DOCUMENT)
        path = home / "skills/example-workflow/SKILL.md"
        path.chmod(0o644)
        original_mode = stat.S_IMODE(path.stat().st_mode)
        _, read = await invoke(tool, cwd, action="read", name="example-workflow")
        result, _ = await invoke(
            tool,
            cwd,
            action="patch",
            name="example-workflow",
            old_text="Read first.",
            new_text="Verify first.",
            revision=read["revision"],
        )
        assert not result.is_error
        if os.name != "nt":
            assert stat.S_IMODE(path.stat().st_mode) == original_mode

    asyncio.run(check())


def test_review_model_enforces_hard_request_limit_without_closing_borrowed_model(tmp_path):
    from types import SimpleNamespace

    from corki.evolution.tools import WriteFence
    from corki.evolution.worker import ReviewModel
    from corki.models import ModelCompleted, ModelError

    configure(tmp_path, True)

    class Model:
        calls = 0

        async def stream(self, request):
            self.calls += 1
            yield ModelCompleted(())

        async def aclose(self):
            pytest.fail("borrowed model must not be closed by reviewer")

    async def check():
        underlying = Model()
        wrapper = ReviewModel(
            underlying, WriteFence(EvolutionSwitch(tmp_path)), CorkiSettings(tmp_path)
        )
        request = SimpleNamespace(instructions="Review", context_items=(), items=(), tools=())
        for _ in range(16):
            assert len([event async for event in wrapper.stream(request)]) == 1
        with pytest.raises(ModelError, match="预算"):
            _ = [event async for event in wrapper.stream(request)]
        assert underlying.calls == 16
        await wrapper.aclose()

    asyncio.run(check())
