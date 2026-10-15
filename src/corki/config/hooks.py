"""Explicit embedding-host hook authority; never parsed from local project layers."""

import json
from dataclasses import dataclass
from pathlib import Path

HOOK_EVENTS = (
    "Stop",
    "SubagentStop",
    "PreToolUse",
    "PostToolUse",
    "PreCompact",
    "PostCompact",
    "UserPromptSubmit",
    "SessionStart",
    "SubagentStart",
    "SessionEnd",
    "Interrupt",
)


@dataclass(frozen=True, slots=True)
class ManagedHookPolicy:
    source: Path
    hooks_json: str = "{}"
    only_managed: bool = False

    def __post_init__(self):
        if not isinstance(self.source, Path) or not self.source.is_absolute():
            raise ValueError("managed hook source must be an absolute host path")
        if type(self.only_managed) is not bool:
            raise ValueError("only_managed must be a host boolean")
        if not isinstance(self.hooks_json, str) or len(self.hooks_json) > 1_000_000:
            raise ValueError("invalid managed hook definitions")
        hooks = json.loads(self.hooks_json)
        if not isinstance(hooks, dict) or hooks.keys() - set(HOOK_EVENTS):
            raise ValueError("unsupported managed hook events")
        for groups in hooks.values():
            if not isinstance(groups, list):
                raise ValueError("managed hook groups must be arrays")
            for group in groups:
                if (
                    not isinstance(group, dict)
                    or group.keys() - {"matcher", "hooks"}
                    or not isinstance(group.get("hooks"), list)
                ):
                    raise ValueError("invalid managed hook group")
