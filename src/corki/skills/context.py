"""Prompt contributions for the bounded catalog and explicitly named skills."""

from __future__ import annotations

from pathlib import Path

from corki.prompting import PromptContribution, PromptRole, PromptSlot
from corki.skills.service import SkillService


class SkillContextContributor:
    """Expose metadata every turn and full bodies only for explicit mentions."""

    def __init__(self, service: SkillService) -> None:
        self._service = service

    def contributions(
        self,
        *,
        cwd: Path,
        user_input: str,
        realtime_active: bool,
    ) -> tuple[PromptContribution, ...]:
        del realtime_active
        contributions = [
            PromptContribution(
                key="extensions.skills.catalog",
                template_name="extensions/skills/catalog",
                role=PromptRole.DEVELOPER,
                slot=PromptSlot.EXTENSIONS,
                variables={"catalog": self._service.render_catalog(cwd)},
            )
        ]
        for index, skill in enumerate(self._service.explicit_mentions(user_input, cwd)):
            try:
                contents = self._service.read(skill)
            except (OSError, UnicodeError, ValueError):
                continue
            contributions.append(
                PromptContribution(
                    key=f"extensions.skills.selected.{skill.qualified_name}",
                    template_name="extensions/skills/selected",
                    role=PromptRole.USER,
                    slot=PromptSlot.EXTENSIONS,
                    order=100 + index,
                    variables={
                        "name": skill.qualified_name,
                        "path": str(skill.path),
                        "contents": contents,
                    },
                    separate_message=True,
                )
            )
        return tuple(contributions)
