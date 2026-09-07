"""Prompt contributions for the bounded catalog and explicitly named skills."""

from __future__ import annotations

from pathlib import Path

from corki.context.extensions import InputContextContributions
from corki.prompting import PromptContribution, PromptRole, PromptSlot
from corki.skills.models import SkillMetadata
from corki.skills.service import SkillService


class SkillContextContributor:
    """Refresh catalog metadata per step and read bodies through the turn-input hook."""

    def __init__(self, service: SkillService, *, include_instructions: bool = True) -> None:
        self._service = service
        self._include_instructions = include_instructions

    def contributions(
        self,
        *,
        cwd: Path,
        user_input: str,
        realtime_active: bool,
    ) -> tuple[PromptContribution, ...]:
        del realtime_active, user_input
        catalog = self._service.catalog(cwd) if self._include_instructions else None
        has_body = catalog is not None and bool(catalog.body)
        return (
            PromptContribution(
                key="extensions.skills.catalog",
                template_name="extensions/skills/catalog" if has_body else None,
                role=PromptRole.DEVELOPER,
                slot=PromptSlot.EXTENSIONS,
                variables={"catalog": catalog.body} if has_body else {},
                warnings=(catalog.report.warning,) if catalog and catalog.report.warning else (),
                snapshot_state="skills.listed" if self._include_instructions else "skills.hidden",
            ),
        )

    def input_contributions(self, *, cwd: Path, user_input: str) -> InputContextContributions:
        """Read explicit bodies only when the harness prepares a turn input."""
        contributions: list[PromptContribution] = []
        warnings: list[str] = []
        for index, skill in enumerate(self._service.explicit_mentions(user_input, cwd)):
            try:
                contents = self._service.read(skill)
            except (OSError, UnicodeError, ValueError) as exc:
                warnings.append(_load_warning(skill, exc))
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
                    input_scoped=True,
                )
            )
        return InputContextContributions(tuple(contributions), tuple(warnings))


def _load_warning(skill: SkillMetadata, exc: Exception) -> str:
    try:
        message = str(exc)
    except Exception:  # noqa: BLE001 - error formatting must not turn a read failure fatal
        message = "exception message unavailable"
    warning = (
        (
            f"Failed to load skill {skill.qualified_name} at {skill.path}: "
            f"{type(exc).__name__}: {message}"
        )
        .encode("utf-8", errors="replace")
        .decode("utf-8")
    )
    return warning if len(warning) <= 4000 else warning[:3997] + "..."
