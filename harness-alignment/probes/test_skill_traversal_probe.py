"""Source-mapped remaining native walk differences, outside passing release tests."""

import pytest

from corki.skills.discovery import SkillRoot, discover_skills
from corki.skills.models import SkillDiscoveryMode, SkillScope


@pytest.mark.parametrize(
    "case", ["agent_file_link", "legacy_file_link", "depth", "hidden", "support"]
)
def test_native_skill_walk_contract(tmp_path, case):
    root = tmp_path / "skills"
    root.mkdir()
    directory = root / (
        "a/b/c/d/e/f/g/h"
        if case == "depth"
        else ".custom"
        if case == "hidden"
        else "references"
        if case == "support"
        else "guide"
    )
    directory.mkdir(parents=True)
    contents = "---\nname: fixture\ndescription: fixture\n---\nBODY"
    if case.endswith("file_link"):
        source = tmp_path / "source.md"
        source.write_text(contents)
        (directory / "SKILL.md").symlink_to(source)
    else:
        (directory / "SKILL.md").write_text(contents)
    snapshot = discover_skills(
        (
            SkillRoot(
                root,
                SkillScope.USER,
                "fixture",
                "fixture@lab",
                tmp_path,
                SkillDiscoveryMode.DIRECT_CHILDREN
                if case == "agent_file_link"
                else SkillDiscoveryMode.RECURSIVE,
            ),
        )
    )
    assert [s.qualified_name for s in snapshot.skills] == (
        ["fixture:fixture"] if case == "support" else []
    )
