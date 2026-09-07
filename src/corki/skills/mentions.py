"""Default host text mention parsing and ordered exact skill selection."""

import re
from collections import Counter

from corki.skills.models import SkillSnapshot

_NAME = r"[A-Za-z0-9_:-]+"
_MENTION = re.compile(
    r"\[\$(?P<link>" + _NAME + r")\][ \t\n\r\f\v]*\((?P<path>[^)]*)\)|\$(?P<plain>" + _NAME + r")"
)
_ENVIRONMENT_NAMES = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "SHELL",
        "PWD",
        "TMPDIR",
        "TEMP",
        "TMP",
        "LANG",
        "TERM",
        "XDG_CONFIG_HOME",
    }
)


def extract(text: str) -> tuple[frozenset[str], frozenset[str]]:
    names, paths = set(), set()
    for match in _MENTION.finditer(text):
        name = match["link"] or match["plain"]
        if name.upper() in _ENVIRONMENT_NAMES:
            continue
        path = (match["path"] or "").strip()
        if match["link"] and path:
            if not path.startswith(("app://", "mcp://", "plugin://")):
                paths.add(path.removeprefix("skill://").replace("\\", "/"))
        else:
            names.add(name)
    return frozenset(names), frozenset(paths)


def select(text: str, snapshot: SkillSnapshot):
    names, paths = extract(text)
    enabled = tuple(skill for skill in snapshot.skills if snapshot.is_enabled(skill))
    counts = Counter(skill.qualified_name for skill in enabled)
    selected, seen_paths, seen_names = [], set(), set()
    for skill in enabled:
        locators = {str(skill.path).replace("\\", "/")}
        if skill.discovery_path is not None:
            locators.add(str(skill.discovery_path).replace("\\", "/"))
        if locators & paths and skill.path not in seen_paths:
            selected.append(skill)
            seen_paths.add(skill.path)
            seen_names.add(skill.qualified_name)
    for skill in enabled:
        name = skill.qualified_name
        if (
            name in names
            and counts[name] == 1
            and name not in seen_names
            and skill.path not in seen_paths
        ):
            selected.append(skill)
            seen_paths.add(skill.path)
            seen_names.add(name)
    return tuple(selected)
