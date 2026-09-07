"""Host skill document parsing, separate from model-visible catalog budgets."""

from __future__ import annotations

from pathlib import Path

import yaml

from corki.skills.frontmatter import parse
from corki.skills.models import SkillMetadata, SkillScope
from corki.skills.policy import allows_implicit_invocation


class SkillParseError(ValueError):
    pass


def parse_skill(
    path: Path,
    *,
    root: Path,
    scope: SkillScope,
    namespace: str | None = None,
) -> SkillMetadata:
    """Read the document as UTF-8 and preserve metadata until catalog allocation."""
    try:
        name, description, short = parse(
            path.read_text(encoding="utf-8-sig"), " ".join(path.parent.name.split()) or "skill"
        )
    except (yaml.YAMLError, ValueError) as exc:
        raise SkillParseError(str(exc)) from exc
    return SkillMetadata(
        name,
        description,
        path.resolve(),
        root.resolve(),
        scope,
        namespace,
        allows_implicit_invocation(path),
        short_description=short,
    )
