"""Default host text mention parsing and ordered exact skill selection."""

import re
from collections import Counter

from corki.config.mcp_headers import RUST_WHITESPACE
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


def linked_paths(text: str, *, sigil: str = "$") -> frozenset[str]:
    """Keep explicit resource paths, including MCP/plugin, using native sigil syntax."""
    matcher = (
        _MENTION if sigil == "$" else re.compile(_MENTION.pattern.replace(r"\$", re.escape(sigil)))
    )
    return frozenset(
        match["path"].strip(RUST_WHITESPACE)
        for match in matcher.finditer(text)
        if match["link"]
        and match["link"].upper() not in _ENVIRONMENT_NAMES
        and match["path"]
        and match["path"].strip(RUST_WHITESPACE)
    )


def select(text: str, snapshot: SkillSnapshot, *, mentions=()):
    """Select host packages, not extension catalog entries with first-name matching."""
    names, paths = extract(text)
    enabled = tuple(skill for skill in snapshot.skills if snapshot.is_enabled(skill))
    selected, seen_paths, blocked_names = [], set(), set()
    seen_names = set()
    name_counts = Counter(skill.qualified_name for skill in enabled)

    def locators(skill):
        values = {str(skill.path).replace("\\", "/")}
        if skill.discovery_path is not None:
            values.add(str(skill.discovery_path).replace("\\", "/"))
        return values

    def push(skill):
        if skill.path not in seen_paths:
            selected.append(skill)
            seen_paths.add(skill.path)
            seen_names.add(skill.qualified_name)

    def select_path(path):
        normalized = path.removeprefix("skill://").replace("\\", "/")
        for skill in enabled:
            if normalized in locators(skill):
                push(skill)
                break

    for mention in mentions:
        path = mention.path
        if mention.kind == "skill":
            blocked_names.add(mention.name)
            select_path(path)
    # Text paths follow discovery order, not lexical path or input mention order.
    for skill in enabled:
        if locators(skill) & paths:
            push(skill)
    for skill in enabled:
        name = skill.qualified_name
        if (
            name in names
            and name not in blocked_names
            and name not in seen_names
            and name_counts[name] == 1
        ):
            push(skill)
    return tuple(selected)
