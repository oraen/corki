"""Prompt contributions for the bounded catalog and explicitly named skills."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from corki.context.extensions import InputContextContributions
from corki.prompting import PromptContribution, PromptPhase, PromptRole, PromptSlot
from corki.skills.io import run_skill_io
from corki.skills.models import SkillMetadata
from corki.skills.service import SkillService


class SkillContextContributor:
    """Refresh catalog metadata per step and read bodies through the turn-input hook."""

    def __init__(
        self,
        service: SkillService,
        *,
        include_instructions: bool = True,
        allow_input_mentions: bool = True,
    ) -> None:
        self._service = service
        self._include_instructions = include_instructions
        self._allow_input_mentions = allow_input_mentions

    def with_context_window(self, tokens: int) -> SkillContextContributor:
        """Retain the admitted Turn's skill budget across later settings changes."""
        return SkillContextContributor(
            self._service.with_context_window(tokens),
            include_instructions=self._include_instructions,
            allow_input_mentions=self._allow_input_mentions,
        )

    def with_service(self, service: SkillService) -> SkillContextContributor:
        return SkillContextContributor(
            service,
            include_instructions=self._include_instructions,
            allow_input_mentions=self._allow_input_mentions,
        )

    async def async_contributions(self, *, cwd, user_input, realtime_active):
        return await run_skill_io(
            self.contributions, cwd=cwd, user_input=user_input, realtime_active=realtime_active
        )

    async def async_input_contributions(self, *, cwd, user_input, mentions, tool_snapshot):
        del tool_snapshot
        return await run_skill_io(
            self.input_contributions_with_mentions,
            cwd=cwd,
            user_input=user_input,
            mentions=mentions,
        )

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
                content_kind="corki.skills.catalog",
                template_name="extensions/skills/catalog" if has_body else None,
                role=PromptRole.DEVELOPER,
                slot=PromptSlot.HOST_SKILLS,
                phase=PromptPhase.WORLD_STATE,
                variables={"catalog": catalog.body} if has_body else {},
                warnings=(catalog.report.warning,) if catalog and catalog.report.warning else (),
                snapshot_state="skills.listed" if self._include_instructions else "skills.hidden",
            ),
        )

    def input_contributions(self, *, cwd: Path, user_input: str) -> InputContextContributions:
        """Read explicit bodies only when the harness prepares a turn input."""
        return self.input_contributions_with_mentions(cwd=cwd, user_input=user_input, mentions=())

    def input_contributions_with_mentions(
        self, *, cwd: Path, user_input: str, mentions
    ) -> InputContextContributions:
        if not self._allow_input_mentions:
            return InputContextContributions()
        contributions: list[PromptContribution] = []
        warnings: list[str] = []
        for index, skill in enumerate(
            self._service.explicit_mentions(
                user_input,
                cwd,
                mentions=mentions,
            )
        ):
            try:
                contents = self._service.read(skill)
            except (OSError, UnicodeError, ValueError) as exc:
                warnings.append(_load_warning(skill, exc))
                continue
            contributions.append(
                PromptContribution(
                    key=(
                        f"extensions.skills.selected.{skill.qualified_name}."
                        + sha256(str(skill.path).encode()).hexdigest()
                    ),
                    content_kind="skills.selected_skill_instructions",
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
