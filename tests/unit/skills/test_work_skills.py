"""Work skills remain discoverable by explicit identity without implicit task matching."""

import asyncio
import json
import shutil

from corki.config.skills import SkillRule
from corki.protocol.tools import ToolCall
from corki.skills.installer import install_bundled_skills
from corki.skills.service import SkillService
from corki.skills.tools import SkillListTool, SkillReadTool
from corki.tools.base import ToolContext


def write(root, name, body="WORK_BODY_SHOULD_NOT_BE_IN_CATALOG"):
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: Collect and organize content\n---\n{body}")
    return path


def test_work_inventory_explicit_reads_and_implicit_exclusion(tmp_path):
    home = tmp_path / "home"
    work = write(home / "skills/work", "collect-intelligence")
    write(home / "skills", "normal-helper")
    service = SkillService(home=home, project_root=tmp_path, bundled_enabled=False)
    snapshot = service.snapshot(tmp_path)
    skill = snapshot.resolve("collect-intelligence")
    assert skill.work_only and not skill.allow_implicit_invocation
    assert not snapshot.is_visible(skill)
    assert len([s for s in snapshot.skills if s.path == work]) == 1
    assert not snapshot.resolve("normal-helper").work_only
    assert snapshot.is_visible(snapshot.resolve("normal-helper"))
    catalog = service.render_catalog(tmp_path)
    assert "collect-intelligence: [explicit-only work skill]" in catalog
    work_line = next(line for line in catalog.splitlines() if "collect-intelligence:" in line)
    assert "Collect and organize content" not in work_line
    assert "WORK_BODY_SHOULD_NOT_BE_IN_CATALOG" not in catalog
    assert service.explicit_mentions("Collect and organize content", tmp_path) == ()
    assert service.explicit_mentions("有没有 collect-intelligence 这个技能？", tmp_path) == ()
    assert service.explicit_mentions("$collect-intelligence", tmp_path) == (skill,)
    context = ToolContext(tmp_path)
    listed = asyncio.run(
        SkillListTool(service).execute(ToolCall("list", "skill_list", {}), context)
    )
    entry = next(row for row in json.loads(listed.content)["skills"] if row["name"] == skill.name)
    assert entry["work_only"] and entry["allow_implicit_invocation"] is False
    result = asyncio.run(
        SkillReadTool(service).execute(
            ToolCall("read", "skill_read", {"name": skill.name}), context
        )
    )
    assert not result.is_error and "WORK_BODY" in result.content


def test_work_punctuation_mentions_ignore_legacy_single_skill_disables(tmp_path):
    home = tmp_path / "home"
    path = write(home / "skills/work", "corki-text-C&E")
    service = SkillService(home=home, project_root=tmp_path, bundled_enabled=False)
    assert [s.name for s in service.explicit_mentions("$corki-text-C&E", tmp_path)] == [
        "corki-text-C&E"
    ]
    assert not service.explicit_mentions("$corki-text-C&E-other", tmp_path)
    service = SkillService(
        home=home,
        project_root=tmp_path,
        bundled_enabled=False,
        rules=(SkillRule(path=path, enabled=False),),
    )
    assert "corki-text-C&E" in service.render_catalog(tmp_path)
    assert service.explicit_mentions("$corki-text-C&E", tmp_path)
    assert not service.snapshot(tmp_path).is_visible(
        service.snapshot(tmp_path).resolve("corki-text-C&E")
    )


def test_bundled_work_migration_preserves_edits_and_deletion(tmp_path):
    home, source = tmp_path / "home", tmp_path / "source"
    write(source, "corki-text-C&E", "bundled initial")
    write(source, "other", "system")
    legacy = write(home / "skills/.system", "corki-text-C&E", "legacy customized")
    install_bundled_skills(home, source)
    target = home / "skills/work/corki-text-C&E/SKILL.md"
    assert "legacy customized" in target.read_text()
    assert not legacy.exists()
    assert (home / "skills/.system/other/SKILL.md").is_file()
    target.write_text("user edited work skill")
    write(source, "corki-text-C&E", "new bundled version")
    install_bundled_skills(home, source)
    assert target.read_text() == "user edited work skill"
    shutil.rmtree(target.parent)
    install_bundled_skills(home, source)
    assert not target.exists()


def test_knowledge_extraction_reads_current_work_skill(tmp_path):
    from corki.knowledge.extraction import extraction_skill

    text = extraction_skill(tmp_path)
    target = tmp_path / "skills/work/corki-text-C&E/SKILL.md"
    assert target.is_file() and text == target.read_text()
    target.write_text("current work instruction")
    assert extraction_skill(tmp_path) == "current work instruction"


def test_alias_into_work_directory_cannot_restore_implicit_visibility(tmp_path):
    home = tmp_path / "home"
    path = write(home / "skills/work", "collect-intelligence")
    try:
        (home / "skills/alias").symlink_to(path.parent, target_is_directory=True)
    except OSError:
        import pytest

        pytest.skip("symbolic links are unavailable in this environment")
    service = SkillService(home=home, project_root=tmp_path, bundled_enabled=False)
    skill = service.snapshot(tmp_path).resolve("collect-intelligence")
    assert skill.work_only and not service.snapshot(tmp_path).is_visible(skill)


def test_legacy_ce_disable_selector_does_not_disable_work_skill(tmp_path):
    from corki.config.skills import parse_skill_rules

    home = tmp_path / "home"
    write(home / "skills/work", "corki-text-C&E")
    rules = parse_skill_rules(
        [{"path": str(home / "skills/.system/corki-text-C&E/SKILL.md"), "enabled": False}], home
    )
    service = SkillService(home=home, project_root=tmp_path, bundled_enabled=False, rules=rules)
    assert service.snapshot(tmp_path).resolve("corki-text-C&E").work_only
