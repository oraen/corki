"""Legacy connector controls cannot suppress ordinary explicit skill selection."""

import pytest

from corki.skills.context import SkillContextContributor
from corki.skills.mentions import select
from corki.skills.service import SkillService


@pytest.mark.parametrize("entry", ["service", "select"])
def test_legacy_connector_override_is_rejected(tmp_path, entry):
    service = SkillService(home=tmp_path / "home", project_root=tmp_path, bundled_enabled=False)
    with pytest.raises(TypeError, match="connector_names"):
        if entry == "service":
            service.explicit_mentions("$gmail", tmp_path, connector_names={"gmail"})
        else:
            select("$gmail", service.snapshot(tmp_path), connector_names={"gmail"})


def test_skill_selection_survives_budget_and_service_views(tmp_path):
    path = tmp_path / ".corki/skills/gmail/SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\nname: gmail\ndescription: fixture\n---\nBODY")
    service = SkillService(home=tmp_path / "home", project_root=tmp_path, bundled_enabled=False)
    original = SkillContextContributor(service)
    assert not hasattr(original, "with_connector_names")
    for view in (original, original.with_context_window(4096), original.with_service(service)):
        selected = view.input_contributions(cwd=tmp_path, user_input="$gmail").contributions
        assert len(selected) == 1
        assert selected[0].variables["name"] == "gmail"
