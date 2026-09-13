"""Host-owned local execution authority, separate from model/MCP approval settings."""

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from corki.config.approval import normalize_approval_policy
from corki.config.exec_policy import ExecPolicySnapshot, ExecPolicySource
from corki.config.execution_requirements import ExecutionRequirementsLayer
from corki.execution.bundled import bundled_compiler


class ExecutionDefault(Enum):
    """Construction-only marker; never retained in admitted settings or checkpoints."""

    AUTO = "auto"


@dataclass(frozen=True, slots=True)
class ActivePermissionProfile:
    """Selected declaration identity; never inferred from concrete permissions."""

    id: str
    extends: str | None = None

    def __post_init__(self) -> None:
        # Native profile declarations permit an empty TOML key; only the
        # reserved ':' prefix is rejected by the native catalog validator.
        if not isinstance(self.id, str):
            raise ValueError("active permission profile needs an id")
        if self.extends is not None and not isinstance(self.extends, str):
            raise ValueError("active permission profile parent must be a string")


@dataclass(frozen=True, slots=True)
class ExecutionPermissions:
    """Immutable explicit profile and its resolution root; never tool arguments.

    The pinned native compiler validates canonical PermissionProfile or compatible
    legacy SandboxPolicy JSON. Managed source layers travel independently of the
    user profile and are resolved at admission, then revalidated at execution.
    An unresolved selection is compiled before admission. Its identity/roots are
    then published with the concrete profile; durable recovery remains separate.
    """

    compiler: Path
    policy_cwd: Path
    profile_json: str
    requirements: tuple[ExecutionRequirementsLayer, ...] = ()
    active_profile: ActivePermissionProfile | None = None
    profile_workspace_roots: tuple[Path, ...] = ()
    catalog_json: str | None = None
    select_from_config: bool = False
    exec_policy_sources: tuple[ExecPolicySource, ...] = ()
    exec_policy_snapshot: ExecPolicySnapshot | None = None
    approval_policy_json: str = '"never"'
    approval_policy_explicit: bool = True
    requested_approval_policy_json: str | None = None
    approval_policy_constraint: str = "configured"

    def __post_init__(self):
        object.__setattr__(
            self, "approval_policy_json", normalize_approval_policy(self.approval_policy_json)
        )
        if type(self.approval_policy_explicit) is not bool:
            raise ValueError("approval selection explicitness must be host-owned")
        if self.requested_approval_policy_json is not None:
            object.__setattr__(
                self,
                "requested_approval_policy_json",
                normalize_approval_policy(self.requested_approval_policy_json),
            )
        if self.approval_policy_constraint not in ("configured", "memory", "guardian"):
            raise ValueError("invalid approval policy constraint")
        if self.approval_policy_constraint != "configured" and (
            self.approval_policy_json != '"never"'
            or self.requested_approval_policy_json not in (None, '"never"')
        ):
            raise ValueError("internal worker approval policy is locked to never")
        if self.exec_policy_snapshot is not None and not isinstance(
            self.exec_policy_snapshot, ExecPolicySnapshot
        ):
            raise ValueError("exec policy snapshot must be host-owned")
        sources = tuple(self.exec_policy_sources)
        if any(not isinstance(source, ExecPolicySource) for source in sources):
            raise ValueError("exec policy sources must be host-owned snapshots")
        object.__setattr__(self, "exec_policy_sources", sources)
        if type(self.select_from_config) is not bool:
            raise ValueError("permission selection mode must be host-owned")
        if self.catalog_json is not None:
            if not isinstance(self.catalog_json, str) or len(self.catalog_json) > 1_000_000:
                raise ValueError("invalid retained permission catalog")
            catalog = json.loads(self.catalog_json)
            if not isinstance(catalog, dict) or catalog.get("type") != "selection":
                raise ValueError("retained permission catalog requires a selection")
        if self.select_from_config and self.catalog_json is None:
            raise ValueError("config selection requires its retained catalog")
        requirements = tuple(self.requirements)
        if any(not isinstance(layer, ExecutionRequirementsLayer) for layer in requirements):
            raise ValueError("execution requirements must be host-owned layers")
        object.__setattr__(self, "requirements", requirements)
        if self.active_profile is not None and not isinstance(
            self.active_profile, ActivePermissionProfile
        ):
            raise ValueError("active profile must be a validated identity")
        roots = tuple(self.profile_workspace_roots)
        if any(not isinstance(path, Path) or not path.is_absolute() for path in roots):
            raise ValueError("profile workspace roots must be absolute paths")
        object.__setattr__(self, "profile_workspace_roots", roots)
        for path in (self.compiler, self.policy_cwd):
            if not isinstance(path, Path) or not path.is_absolute():
                raise ValueError("sandbox compiler and policy cwd must be absolute paths")
        if not isinstance(self.profile_json, str) or len(self.profile_json) > 1_000_000:
            raise ValueError("invalid sandbox permission profile")
        try:
            profile = json.loads(self.profile_json)
        except (ValueError, RecursionError):
            raise ValueError("invalid sandbox permission profile JSON") from None
        if not isinstance(profile, dict) or not isinstance(profile.get("type"), str):
            raise ValueError("sandbox permission profile requires an object with a type")

    @property
    def needs_resolution(self) -> bool:
        """Selections must become concrete before model/tool/worker admission."""
        return json.loads(self.profile_json)["type"] == "selection"


def parse_execution_permissions(
    value: object,
    cwd: Path,
    *,
    configuration: dict[str, object] | None = None,
    project_trust: str | None = None,
) -> ExecutionPermissions:
    """Resolve implicit or explicit policy; absence never means unrestricted execution."""
    if value is None:
        value = {}
        configuration = configuration if configuration is not None else {}
    if isinstance(value, dict) and set(value) <= {"profile"}:
        compiler = bundled_compiler()
        if compiler is None:
            raise ValueError(
                "this installation has no bundled compiler; install a native platform wheel, "
                "run native/sandbox/install.py for a source checkout, "
                "or configure execution.compiler"
            )
        value = {**(value or {}), "compiler": str(compiler)}
    if project_trust not in {None, "trusted", "untrusted"}:
        raise ValueError("invalid host project trust")
    approval = (configuration or {}).get(
        "approval_policy", "untrusted" if project_trust == "untrusted" else "on-request"
    )
    if (configuration or {}).get("approval_policy") == "untrusted":
        raise ValueError(
            'approval_policy = "untrusted" is no longer supported; remove this setting'
        )
    approval_json = json.dumps(approval, allow_nan=False)
    if isinstance(value, dict) and set(value) == {"compiler"} and configuration is not None:
        compiler = value["compiler"]
        if not isinstance(compiler, str) or not compiler:
            raise ValueError("execution.compiler must be an absolute path")
        if "sandbox_mode" in configuration or "sandbox_workspace_write" in configuration:
            raise ValueError("legacy selection syntax is not yet supported; use execution.profile")
        selection = {"type": "selection"}
        if project_trust is not None:
            selection["project_trust"] = project_trust
        for key in ("default_permissions", "permissions"):
            if key in configuration:
                selection[key] = configuration[key]
        return ExecutionPermissions(
            Path(compiler),
            cwd,
            json.dumps(selection, allow_nan=False),
            approval_policy_json=approval_json,
            approval_policy_explicit="approval_policy" in (configuration or {}),
        )
    if not isinstance(value, dict) or set(value) != {"compiler", "profile"}:
        raise ValueError("execution requires compiler and profile")
    if configuration and ("permissions" in configuration or "default_permissions" in configuration):
        raise ValueError("execution.profile cannot be combined with named permission selection")
    compiler = value["compiler"]
    if not isinstance(compiler, str) or not compiler:
        raise ValueError("execution.compiler must be an absolute path")
    return ExecutionPermissions(
        Path(compiler),
        cwd,
        json.dumps(value["profile"], allow_nan=False),
        approval_policy_json=approval_json,
        approval_policy_explicit="approval_policy" in (configuration or {}),
    )
