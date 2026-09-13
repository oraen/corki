"""Ordered name/path skill enablement rules, separate from prompt visibility."""

import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from corki.config.layers import LocalConfigState

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SkillRule:
    enabled: bool
    name: str | None = None
    path: Path | None = None
    source: Literal["host", "layers"] = field(default="host", compare=False, repr=False)

    def __post_init__(self):
        if self.source not in {"host", "layers"}:
            raise ValueError("invalid skill rule source")
        if not isinstance(self.enabled, bool) or (self.name is None) == (self.path is None):
            raise ValueError("skill rule requires enabled and exactly one name/path selector")
        if self.path is not None:
            object.__setattr__(self, "path", self.path.expanduser().resolve())


def parse_skill_rules(value, base: Path, *, source="host") -> tuple[SkillRule, ...]:
    if not isinstance(value, list) or any(
        not isinstance(entry, dict)
        or not isinstance(entry.get("enabled"), bool)
        or any(key in entry and not isinstance(entry[key], str) for key in ("name", "path"))
        for entry in value
    ):
        _LOG.warning("ignoring invalid skills.config rules")
        return ()
    rules = []
    for entry in value:
        name, path = entry.get("name"), entry.get("path")
        if (name is None) == (path is None) or (name is not None and not name.strip()):
            _LOG.warning("ignoring skills.config entry without exactly one nonempty selector")
            continue
        rule = SkillRule(
            entry["enabled"],
            name.strip() if name is not None else None,
            (base / Path(path).expanduser()) if path is not None else None,
            source=source,
        )
        rules = [old for old in rules if (old.name, old.path) != (rule.name, rule.path)]
        rules.append(rule)
    return tuple(rules)


def skill_rules_from_layers(configuration: LocalConfigState) -> tuple[SkillRule, ...]:
    """User-only, source-relative rules; explicit host rules are applied separately.

    Do not merge the config arrays: unrelated selectors in lower user/profile
    layers survive, and a repository cannot re-enable a user-disabled skill.
    """
    rules = []
    for layer in configuration.layers:
        if layer.kind != "user" or layer.disabled_reason is not None:
            continue
        skills = tomllib.loads(layer.contents).get("skills", {})
        if not _valid_skills_table(skills):
            _LOG.warning("ignoring invalid skills config")
            continue
        for rule in parse_skill_rules(skills.get("config", []), layer.file.parent, source="layers"):
            rules = [old for old in rules if (old.name, old.path) != (rule.name, rule.path)]
            rules.append(rule)
    return tuple(rules)


def _valid_skills_table(skills) -> bool:
    """Native typed SkillsConfig rejects the whole layer on a malformed field."""
    if not isinstance(skills, dict):
        return False
    if "include_instructions" in skills and type(skills["include_instructions"]) is not bool:
        return False
    if "max_context_tokens" in skills and (
        type(skills["max_context_tokens"]) is not int or skills["max_context_tokens"] <= 0
    ):
        return False
    bundled = skills.get("bundled", {})
    return isinstance(bundled, dict) and type(bundled.get("enabled", True)) is bool


def disabled_skill_paths(rules: tuple[SkillRule, ...], identities) -> frozenset[Path]:
    identities = tuple(identities)
    disabled = set()
    for rule in rules:
        paths = (
            (rule.path,)
            if rule.path is not None
            else (path for name, path in identities if name == rule.name)
        )
        for path in paths:
            if rule.enabled:
                disabled.discard(path)
            else:
                disabled.add(path)
    return frozenset(disabled)
