import hashlib
import json

from corki.config.skills import SkillRule
from corki.evolution.migration import migrate_legacy_skills
from corki.evolution.state import EvolutionState
from corki.evolution.switch import EvolutionSwitch
from corki.evolution.tools import SkillManageTool
from corki.skills.service import SkillService


def make_skill(home, directory, name):
    root = home / directory / name
    root.mkdir(parents=True)
    content = f"---\nname: {name}\ndescription: Fixture procedure.\n---\nGuide\n"
    (root / "SKILL.md").write_text(content)
    (root / "resource.txt").write_text("preserved")
    return root / "SKILL.md"


def record(home, name, before=None, applied=True):
    state = EvolutionState(home)
    identity = state.prepare_edit(name, "SKILL.md", before, "fixture", "review", None)
    with state.database() as db:
        db.execute("UPDATE mutations SET policy_version=1 WHERE id=?", (identity,))
    if applied:
        state.applied(identity)
    return state


def test_migrate_created_and_edited_only_preserves_policy_ids_hooks_and_history(tmp_path):
    home = tmp_path / "home"
    created = make_skill(home, "skills", "created")
    edited = make_skill(home, "skills", "edited")
    work = make_skill(home, "skills/work", "work-edited")
    manual_work = make_skill(home, "skills/work", "manual-work")
    record(home, "work-edited", before="old procedure")
    manual = make_skill(home, "skills", "manual")
    uncommitted = make_skill(home, "skills", "uncommitted")
    state = record(home, "created")
    record(home, "edited", before="previous version")
    record(home, "uncommitted", applied=False)
    config = {"features": {"self_evolution": False},
              "skills": {"config": [{"path": str(created), "enabled": False}]},
              "hooks": {"Stop": [{"hooks": [{"type": "skill", "path": str(edited)}]}]}}
    (home / "settings.json").write_text(json.dumps(config))
    previous = state.history("edited", "SKILL.md")
    service = SkillService(home=home, project_root=tmp_path, bundled_enabled=False,
                           rules=(SkillRule(False, path=created),))
    paths = {s.name: s.path for s in service.snapshot(tmp_path).skills}
    assert paths["created"] == home / "skills/self-improving/created/SKILL.md"
    assert paths["edited"] == home / "skills/self-improving/edited/SKILL.md"
    assert paths["work-edited"] == home / "skills/self-improving/work-edited/SKILL.md"
    assert not work.exists() and manual_work.exists()
    assert manual.exists() and uncommitted.exists()
    assert not created.exists() and not edited.exists()
    assert (paths["created"].parent / "resource.txt").read_text() == "preserved"
    assert paths["created"] in service.snapshot(tmp_path).disabled_paths
    changed = json.loads((home / "settings.json").read_text())
    assert changed["features"]["self_evolution"] is False
    assert changed["hooks"]["Stop"][0]["hooks"][0]["path"] == str(paths["edited"])
    mapping = json.loads((home / "manager/skill-locations.json").read_text())
    assert mapping[str(paths["created"])] == hashlib.sha256(str(created).encode()).hexdigest()
    assert previous == state.history("edited", "SKILL.md")
    # Location no longer decides whether learning may improve a skill.
    tool = SkillManageTool(home, service, tmp_path, EvolutionSwitch(home))
    snapshot = service.snapshot(tmp_path)
    assert tool._editable(next(s for s in snapshot.skills if s.name == "manual"))
    assert tool._editable(next(s for s in snapshot.skills if s.name == "edited"))
    assert not next(s for s in snapshot.skills if s.name == "edited").work_only


def test_conflict_keeps_both_packages_and_can_retry_after_resolution(tmp_path):
    home = tmp_path / "home"
    source = make_skill(home, "skills", "example")
    target = make_skill(home, "skills/self-improving", "example")
    record(home, "example")
    (home / "settings.json").write_text("{}")
    migrate_legacy_skills(home, tmp_path)
    assert source.exists() and target.exists()
    assert not json.loads((home / "evolution/self-improving-v2.json").read_text())["complete"]
    other = target.parent.with_name("other")
    target.parent.rename(other)
    changed = (other / "SKILL.md").read_text().replace("name: example", "name: other")
    (other / "SKILL.md").write_text(changed)
    migrate_legacy_skills(home, tmp_path)
    assert not source.exists() and target.exists()
    assert json.loads((home / "evolution/self-improving-v2.json").read_text())["complete"]


def test_completed_migration_does_not_move_a_skill_back_after_explicit_user_relocation(tmp_path):
    home = tmp_path / "home"
    source = make_skill(home, "skills", "example")
    record(home, "example")
    (home / "settings.json").write_text("{}")
    migrate_legacy_skills(home, tmp_path)
    new = home / "skills/self-improving/example"
    new.rename(source.parent)
    migrate_legacy_skills(home, tmp_path)
    assert source.exists() and not new.exists()


def test_missing_or_broken_evolution_database_does_not_break_skill_discovery(tmp_path):
    home = tmp_path / "home"
    original = make_skill(home, "skills", "manual")
    assert migrate_legacy_skills(home, tmp_path) == {}
    (home / "evolution").mkdir()
    (home / "evolution/evolution.sqlite").write_bytes(b"invalid database")
    service = SkillService(home=home, project_root=tmp_path, bundled_enabled=False)
    assert any(s.path == original for s in service.snapshot(tmp_path).skills)


def test_previous_wrong_directory_is_relocated_and_discovery_never_scans_home(tmp_path):
    home = tmp_path / "home"
    old = make_skill(home, "self-improving", "previous-location")
    (home / "settings.json").write_text(json.dumps({
        "skills": {"config": [{"path": str(old), "enabled": False}]}
    }))
    (home / "SKILL.md").write_text(
        "---\nname: not-a-skill-root\ndescription: Must not be discovered\n---\n"
    )
    service = SkillService(home=home, project_root=tmp_path, bundled_enabled=False,
                          rules=(SkillRule(False, path=old),))
    snapshot = service.snapshot(tmp_path)
    assert len(snapshot.skills) == 1
    current = snapshot.skills[0]
    assert current.path == home / "skills/self-improving/previous-location/SKILL.md"
    assert current.path in snapshot.disabled_paths
    assert not old.exists() and not (home / "self-improving").exists()
    assert {r.path for r in service._roots(tmp_path)}.isdisjoint({home, home / "self-improving"})
    assert current.scope.value == "user" and not current.work_only


def test_v1_aliases_are_chained_to_correct_location_without_changing_ids(tmp_path):
    home = tmp_path / "home"
    old = make_skill(home, "self-improving", "learned")
    original = home / "skills/learned/SKILL.md"
    record(home, "learned")
    (home / "settings.json").write_text("{}")
    old_id = hashlib.sha256(str(original).encode()).hexdigest()
    (home / "manager").mkdir()
    (home / "manager/skill-locations.json").write_text(json.dumps({str(old): old_id}))
    (home / "evolution/self-improving-v1.json").write_text(json.dumps({
        "version": 1, "complete": True, "paths": {str(original): str(old)}
    }))
    aliases = migrate_legacy_skills(home, tmp_path)
    current = home / "skills/self-improving/learned/SKILL.md"
    assert aliases[old] == current and aliases[original] == current
    mapping = json.loads((home / "manager/skill-locations.json").read_text())
    assert mapping[str(current)] == old_id
