from dataclasses import replace
from pathlib import Path

import pytest

from corki.config import CorkiSettings
from corki.context.world_state import changed_context_items, render_context_history
from corki.protocol.ids import new_turn_id
from corki.protocol.items import ContextItem, ContextRole
from corki.skills import SkillService
from corki.skills.catalog import CatalogReport, MetadataBudget, render_catalog, shorten
from corki.skills.models import SkillMetadata, SkillScope, SkillSnapshot


def metadata(name, description="d" * 1024, root=Path("/shared/skills"), scope=SkillScope.PROJECT):
    return SkillMetadata(name, description, root / name / "SKILL.md", root, scope)


def test_catalog_shares_fallback_budget_without_cutting_off_names(tmp_path, monkeypatch):
    skills = tuple(metadata(f"skill-{i:02}") for i in range(20))
    monkeypatch.setattr(SkillService, "snapshot", lambda *args: SkillSnapshot(skills))
    service = SkillService(home=tmp_path, project_root=tmp_path, bundled_enabled=False)
    rendered = service.render_catalog(tmp_path)
    lines = [line for line in rendered.splitlines() if line.startswith("- skill-")]
    assert len(lines) == 20
    assert sum(len(line) + 1 for line in lines) <= 8000
    assert all(line.endswith("/SKILL.md)") for line in lines)


def test_empty_catalog_does_not_advertise_an_empty_skill_surface(tmp_path):
    service = SkillService(home=tmp_path, project_root=tmp_path, bundled_enabled=False)
    assert service.render_catalog(tmp_path) == ""


@pytest.mark.parametrize(
    "window,configured,expected",
    [
        (100_000, None, MetadataBudget(2000)),
        (400_000, None, MetadataBudget(8000)),
        (None, None, MetadataBudget(8000, False)),
        (0, None, MetadataBudget(8000, False)),
        (1, None, MetadataBudget(1)),
        (100_000, 5000, MetadataBudget(5000)),
        (None, 50_000, MetadataBudget(10_000)),
    ],
)
def test_budget_uses_codex_window_ratio_override_and_fallback(window, configured, expected):
    assert MetadataBudget.resolve(window, configured) == expected


def no_alias(skill):
    return replace(skill, root=Path("/" + "unhelpful-root" * 500))


def test_round_robin_keeps_every_locator_before_sharing_description_space():
    skills = (no_alias(metadata("one", "abcdefghij")), no_alias(metadata("two", "0123456789")))
    minimum = sum(len(f"- {s.name}: (file: {s.path})\n") for s in skills)
    result = render_catalog(skills, MetadataBudget(minimum + 9, False))
    assert result.lines == (
        f"- one: abcd (file: {skills[0].path})",
        f"- two: 012 (file: {skills[1].path})",
    )
    assert result.metadata_cost == minimum + 9
    assert result.report == CatalogReport(2, 2, 0, 13, 2)


@pytest.mark.parametrize("tokens", [False, True])
def test_unicode_budget_keeps_complete_lines_and_character_boundaries(tokens):
    skills = tuple(no_alias(metadata(f"s{i}", "💡汉a" * 300)) for i in range(3))
    budget = MetadataBudget(240, tokens)
    result = render_catalog(skills, budget)
    assert result.metadata_cost <= budget.limit
    assert result.metadata_cost == sum(budget.cost(line + "\n") for line in result.lines)
    assert len(result.lines) == 3
    assert all(line.endswith("/SKILL.md)") for line in result.lines)
    result.body.encode("utf-8")


def test_oversized_minimum_does_not_hide_a_later_smaller_skill():
    large = no_alias(metadata("a", root=Path("/" + "x" * 1000)))
    small = no_alias(metadata("z", root=Path("/s")))
    result = render_catalog((large, small), MetadataBudget(60, False))
    assert result.lines == ("- z: (file: /s/z/SKILL.md)",)
    assert result.report.omitted_count == 1
    assert "1 additional skill was" in result.report.warning


def test_all_omitted_host_catalog_retains_its_report_and_empty_header():
    result = render_catalog((no_alias(metadata("a")),), MetadataBudget(1))
    assert result.lines == () and "### Available skills" in result.body
    assert result.report.omitted_count == 1
    assert result.report.warning is not None


def test_description_ceiling_and_warning_threshold_match_codex():
    result = render_catalog((no_alias(metadata("a", "d" * 1500)),), MetadataBudget(10_000))
    assert "d" * 1021 + "... (file:" in result.lines[0]
    assert result.report.truncated_description_chars == 0  # initial 1024-char cap is separate
    assert CatalogReport(2, 2, 0, 200, 2).warning is None
    assert CatalogReport(2, 2, 0, 201, 2).warning is not None


def test_aliases_expand_to_exact_paths_and_charge_the_root_table():
    root = Path("/a-very-long-workspace" * 6) / "skills"
    skills = tuple(metadata(f"s{i}", "brief", root) for i in range(12))
    result = render_catalog(skills, MetadataBudget(350))
    assert result.report.included_count == 12 and result.roots
    assert result.metadata_cost <= 350
    aliases = dict(result.roots)
    for skill in skills:
        line = next(line for line in result.lines if line.startswith(f"- {skill.name}:"))
        locator = line.split("(file: ")[1][:-1]
        alias, suffix = locator.split("/", 1)
        assert Path(aliases[alias]) / suffix == skill.path
    assert shorten("/somewhere/else/SKILL.md", result.roots) == "/somewhere/else/SKILL.md"
    assert shorten("/r/deeper/a", (("r0", "/r"), ("r1", "/r/deeper"))) == "r1/a"
    assert shorten("/roots2/a", (("r0", "/roots"),)) == "/roots2/a"


def test_alias_roots_follow_discovery_order_not_scope_display_order():
    project = metadata("p", root=Path("/project/" + "long" * 30))
    system = metadata("s", root=Path("/system/" + "long" * 30), scope=SkillScope.SYSTEM)
    result = render_catalog((project, system), MetadataBudget(10_000))
    assert result.roots[0] == ("r0", str(project.root))
    assert result.lines[0].startswith("- s:")


def test_single_skill_plugin_versions_share_marketplace_alias_root():
    marketplace = Path("/home/plugins/cache/marketplace")
    skills = tuple(
        metadata(f"s{i}", "brief", marketplace / f"plugin{i}/1.0/skills") for i in range(12)
    )
    result = render_catalog(skills, MetadataBudget(1000))
    assert result.roots == (("r0", str(marketplace)),)


@pytest.mark.parametrize("invalid", [0, -1, True, 1.5, "2000"])
def test_invalid_configured_budget_is_rejected(tmp_path, invalid):
    with pytest.raises(ValueError, match="skills.max_context_tokens"):
        CorkiSettings(working_directory=tmp_path, skills_max_context_tokens=invalid)


def test_toml_budget_reaches_skill_settings(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("[skills]\nmax_context_tokens = 50000\n", encoding="utf-8")
    settings = CorkiSettings.for_directory(tmp_path, config_file=config)
    assert settings.skills_max_context_tokens == 50_000


def test_catalog_updates_append_full_fragments_and_removal_is_specific():
    turn = new_turn_id()
    before = ContextItem(
        "extensions.skills.catalog",
        ContextRole.DEVELOPER,
        "<skills_instructions>old</skills_instructions>",
        turn,
    )
    after = replace(before, content="<skills_instructions>new</skills_instructions>")
    updated = changed_context_items((before,), (after,), turn)
    assert len(updated) == 1 and updated[0].content == after.content
    assert render_context_history((before, *updated))[0] == before
    assert changed_context_items((before, *updated), (after,), turn) == ()
    removed = changed_context_items((before, *updated), (), turn)
    assert len(removed) == 1 and "No host skills are currently available." in removed[0].content
    assert removed[0].snapshot_content == ""
    assert changed_context_items((before, *updated, *removed), (), turn) == ()
