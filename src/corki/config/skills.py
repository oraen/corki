"""Ordered name/path skill enablement rules, separate from prompt visibility."""

import logging
from dataclasses import dataclass
from pathlib import Path

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SkillRule:
    enabled: bool
    name: str | None = None
    path: Path | None = None

    def __post_init__(self):
        if not isinstance(self.enabled, bool) or (self.name is None) == (self.path is None):
            raise ValueError("skill rule requires enabled and exactly one name/path selector")
        if self.path is not None:
            object.__setattr__(self, "path", self.path.expanduser().resolve())


def parse_skill_rules(value, base: Path) -> tuple[SkillRule, ...]:
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
        )
        rules = [old for old in rules if (old.name, old.path) != (rule.name, rule.path)]
        rules.append(rule)
    return tuple(rules)


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
