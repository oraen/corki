import asyncio
import os
import subprocess
import sys
from pathlib import Path

from corki.context import ContextBuilder
from corki.protocol.ids import ToolCallId, new_turn_id
from corki.protocol.tools import ToolCall
from corki.skills import SkillReadTool, SkillService
from corki.skills.models import SkillScope
from corki.tools import ToolContext


def _write_skill(root: Path, folder: str, name: str, body: str = "instructions") -> None:
    directory = root / folder
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test {name}\n---\n\n{body}\n",
        encoding="utf-8",
    )


def test_bundled_catalog_contains_exactly_the_ten_supported_skills(tmp_path: Path) -> None:
    service = SkillService(home=tmp_path / ".corki", project_root=tmp_path)

    snapshot = service.snapshot(tmp_path)

    assert {skill.name for skill in snapshot.skills} == {
        "arxiv",
        "grounded-citations",
        "imagegen",
        "openai-docs",
        "pdf",
        "plugin-creator",
        "review-agent",
        "skill-creator",
        "skill-installer",
        "xlsx",
    }
    assert all(skill.scope is SkillScope.SYSTEM for skill in snapshot.skills)
    assert snapshot.errors == ()
    arxiv = snapshot.resolve("arxiv")
    assert arxiv is not None
    assert "sortBy=submittedDate" in service.read(arxiv)


def test_grounded_citations_uses_corki_home_and_executes_end_to_end(tmp_path: Path) -> None:
    home = tmp_path / ".corki"
    service = SkillService(home=home, project_root=tmp_path)
    skill = service.snapshot(tmp_path).resolve("grounded-citations")
    assert skill is not None
    script = skill.path.parent / "scripts" / "sources.py"
    env = {**os.environ, "CORKI_HOME": str(home)}

    reset = subprocess.run(
        [sys.executable, str(script), "reset"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    added = subprocess.run(
        [sys.executable, str(script), "add", "https://example.com/report", "--title", "Report"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    rendered = subprocess.run(
        [sys.executable, str(script), "render"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert reset.returncode == 0, reset.stderr
    assert added.returncode == 0, added.stderr
    assert "[1] https://example.com/report" in added.stdout
    assert rendered.returncode == 0, rendered.stderr
    assert "[1] https://example.com/report — Report" in rendered.stdout
    assert (home / "cache" / "citations" / "ledger.json").is_file()


def test_general_bundled_skills_use_corki_tool_names(tmp_path: Path) -> None:
    service = SkillService(home=tmp_path / ".corki", project_root=tmp_path)
    snapshot = service.snapshot(tmp_path)

    for name in ("grounded-citations", "pdf", "xlsx"):
        skill = snapshot.resolve(name)
        assert skill is not None
        contents = service.read(skill)
        assert "`terminal`" not in contents
        assert "HERMES_HOME" not in contents

    pdf = snapshot.resolve("pdf")
    assert pdf is not None
    assert "`view_image`" in service.read(pdf)


def test_project_skill_overrides_user_and_system_skill(tmp_path: Path) -> None:
    home = tmp_path / ".corki"
    _write_skill(home / "skills", "arxiv-user", "arxiv", "user version")
    _write_skill(tmp_path / ".corki" / "skills", "arxiv-project", "arxiv", "project version")
    service = SkillService(home=home, project_root=tmp_path)

    skill = service.snapshot(tmp_path).resolve("arxiv")

    assert skill is not None
    assert skill.scope is SkillScope.PROJECT
    assert "project version" in service.read(skill)


def test_codex_compatible_project_and_user_skill_roots_are_discovered(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    cwd = project / "nested"
    user_home = tmp_path / "user"
    cwd.mkdir(parents=True)
    _write_skill(user_home / ".agents" / "skills", "shared", "shared", "user agents")
    _write_skill(project / ".codex" / "skills", "codex-project", "codex-project")
    _write_skill(project / ".agents" / "skills", "shared", "shared", "project agents")
    service = SkillService(
        home=user_home / ".corki",
        project_root=project,
        compatibility_home=user_home,
        bundled_enabled=False,
    )

    snapshot = service.snapshot(cwd)

    assert snapshot.resolve("codex-project") is not None
    shared = snapshot.resolve("shared")
    assert shared is not None
    assert shared.scope is SkillScope.PROJECT
    assert "project agents" in service.read(shared)


def test_native_corki_skill_root_wins_over_compatible_project_roots(tmp_path: Path) -> None:
    _write_skill(tmp_path / ".corki" / "skills", "native", "same", "corki")
    _write_skill(tmp_path / ".codex" / "skills", "compatible", "same", "codex")
    _write_skill(tmp_path / ".agents" / "skills", "portable", "same", "agents")
    service = SkillService(
        home=tmp_path / "home",
        project_root=tmp_path,
        bundled_enabled=False,
    )

    skill = service.snapshot(tmp_path).resolve("same")

    assert skill is not None
    assert "corki" in service.read(skill)


def test_user_agents_skill_directory_symlink_is_followed_once(tmp_path: Path) -> None:
    user_home = tmp_path / "user"
    checkout = tmp_path / "shared-checkout"
    _write_skill(checkout, "portable", "portable", "linked instructions")
    root = user_home / ".agents" / "skills"
    root.mkdir(parents=True)
    (root / "linked").symlink_to(checkout, target_is_directory=True)
    # A cycle must not make discovery recurse forever.
    (checkout / "cycle").symlink_to(root, target_is_directory=True)
    service = SkillService(
        home=user_home / ".corki",
        project_root=tmp_path,
        compatibility_home=user_home,
        bundled_enabled=False,
    )

    snapshot = service.snapshot(tmp_path)

    skill = snapshot.resolve("portable")
    assert skill is not None
    assert skill.path == (checkout / "portable" / "SKILL.md").resolve()
    assert "linked instructions" in service.read(skill)


def test_explicit_skill_mention_loads_full_body_as_a_separate_context_item(
    tmp_path: Path,
) -> None:
    service = SkillService(home=tmp_path / ".corki", project_root=tmp_path)
    from corki.skills.context import SkillContextContributor

    builder = ContextBuilder(contributors=(SkillContextContributor(service),))

    snapshot = asyncio.run(
        builder.build(cwd=tmp_path, turn_id=new_turn_id(), user_input="请使用 $arxiv 找论文")
    )

    selected = [item for item in snapshot.items if "<skill>" in item.content]
    assert len(selected) == 1
    assert "<name>arxiv</name>" in selected[0].content
    assert "# arXiv Research" in selected[0].content


def test_skill_read_rejects_path_escape(tmp_path: Path) -> None:
    service = SkillService(home=tmp_path / ".corki", project_root=tmp_path)
    tool = SkillReadTool(service)
    call = ToolCall(
        ToolCallId("read-1"),
        "skill_read",
        {"name": "arxiv", "file": "../outside.txt"},
    )

    result = asyncio.run(tool.execute(call, ToolContext(cwd=tmp_path)))

    assert result.is_error
    assert "relative path" in result.content
