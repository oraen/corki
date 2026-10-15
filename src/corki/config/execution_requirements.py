"""Immutable, source-relative managed execution fragments supplied by the host."""

import json
from dataclasses import dataclass
from pathlib import Path

from corki.config.approval import normalize_approval_policy


@dataclass(frozen=True, slots=True)
class ExecutionRequirementsLayer:
    """Keep layers separate: native deny-read composition is additive, not replacement."""

    source: str
    value_json: str
    base_dir: Path | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("execution requirements need a source")
        if self.base_dir is not None and (
            not isinstance(self.base_dir, Path) or not self.base_dir.is_absolute()
        ):
            raise ValueError("execution requirement base directory must be absolute")
        if not isinstance(self.value_json, str) or len(self.value_json) > 1_000_000:
            raise ValueError("invalid managed execution requirements")
        validate_execution_requirements(json.loads(self.value_json))


def validate_execution_requirements(value: object) -> None:
    """Accept only implemented domains; native parsing owns paths and mode semantics."""
    if not isinstance(value, dict) or value.keys() - {
        "allowed_sandbox_modes",
        "allowed_approval_policies",
        "permissions",
        "allowed_permission_profiles",
        "default_permissions",
        "rules",
    }:
        raise ValueError("Unsupported managed execution requirement")
    if "allowed_approval_policies" in value and not isinstance(
        value["allowed_approval_policies"], list
    ):
        raise ValueError("allowed_approval_policies must be an array")
    for policy in value.get("allowed_approval_policies", []):
        try:
            normalize_approval_policy(json.dumps(policy))
        except ValueError as exc:
            raise ValueError(f"invalid allowed_approval_policies: {exc}") from exc
    if "rules" in value and not isinstance(value["rules"], dict):
        raise ValueError("managed rules must be a table")
    modes = value.get("allowed_sandbox_modes", [])
    if not isinstance(modes, list) or any(not isinstance(mode, str) for mode in modes):
        raise ValueError("allowed_sandbox_modes must be a string array")
    permissions = value.get("permissions", {})
    if not isinstance(permissions, dict):
        raise ValueError("managed permissions must be a table")
    for name, profile in permissions.items():
        if name == "filesystem":
            continue
        if not isinstance(profile, dict):
            raise ValueError("managed named profile must be a table")
        if "network" in profile and not isinstance(profile["network"], dict):
            raise ValueError("managed profile network must be a table")
    allowed = value.get("allowed_permission_profiles", {})
    if not isinstance(allowed, dict) or any(type(flag) is not bool for flag in allowed.values()):
        raise ValueError("allowed_permission_profiles must be a boolean table")
    if "default_permissions" in value and not isinstance(value["default_permissions"], str):
        raise ValueError("managed default_permissions must be a string")
    filesystem = permissions.get("filesystem", {})
    if not isinstance(filesystem, dict) or filesystem.keys() - {"deny_read"}:
        raise ValueError("Unsupported managed filesystem requirement")
    denied = filesystem.get("deny_read", [])
    if not isinstance(denied, list) or any(not isinstance(path, str) for path in denied):
        raise ValueError("permissions.filesystem.deny_read must be a string array")


def validate_effective_approval_requirements(
    layers: tuple[ExecutionRequirementsLayer, ...],
) -> None:
    """Only the final list must be nonempty; higher-priority arrays replace lower ones.

    Native ConfigRequirements still owns policy resolution and command admission.
    This allocation-free guard runs before creating Runtime/CLI resource owners.
    """
    for layer in reversed(layers):
        values = json.loads(layer.value_json)
        if "allowed_approval_policies" in values:
            if not values["allowed_approval_policies"]:
                raise ValueError(
                    f"Invalid requirements from {layer.source}: "
                    "allowed_approval_policies must not be empty"
                )
            return
