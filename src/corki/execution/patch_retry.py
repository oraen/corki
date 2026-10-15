"""Patch-specific native admission; shell retry has different OnRequest semantics."""

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PatchRetryPlan:
    sandbox: str
    command: tuple[str, ...] | None


def parse_patch_retry(value: dict, compiler: Path, approval_policy_json: str) -> PatchRetryPlan:
    if type(value.get("native_patch_retry")) is not int or value["native_patch_retry"] != 1:
        raise ValueError("sandbox compiler does not support native patch retry v1")
    raw = value.get("patch_retry")
    if not isinstance(raw, dict) or set(raw) != {"sandbox", "command"}:
        raise ValueError("invalid native patch retry plan")
    sandbox, command = raw["sandbox"], raw["command"]
    if sandbox not in ("none", "seatbelt", "seccomp", "windows_sandbox") or sandbox != value.get(
        "sandbox"
    ):
        raise ValueError("invalid actual patch sandbox")
    if command is None:
        return PatchRetryPlan(sandbox, None)
    if (
        not isinstance(command, list)
        or len(command) != 2
        or not isinstance(command[0], str)
        or not Path(command[0]).is_absolute()
        or Path(command[0]).resolve() != compiler.resolve()
        or command[1] != "--corki-fs-helper"
        or sandbox == "none"
    ):
        raise ValueError("patch retry must use the fixed native helper")
    policy = json.loads(approval_policy_json)
    if policy not in ("untrusted", "on-request") and not (
        isinstance(policy, dict) and policy["granular"]["sandbox_approval"] is True
    ):
        raise ValueError("patch retry contradicts admitted approval policy")
    return PatchRetryPlan(sandbox, tuple(command))
