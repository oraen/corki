"""Host-only managed MCP loading and immutable, source-bearing policy snapshots.

MCP requirements are composed here. Execution constraints stay source-relative
until native Runtime admission. Other global domains still fail explicitly.
"""

import json
import tomllib
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from corki.config.execution_requirements import (
    ExecutionRequirementsLayer,
    validate_effective_approval_requirements,
)
from corki.config.managed_instructions import ManagedDeveloperInstructions
from corki.config.managed_paths import system_requirements_path
from corki.config.mcp_requirements import MCPRequirements
from corki.config.mcp_shapes import validate_requirements_shape


@dataclass(frozen=True, slots=True)
class MCPRequirementsLayer:
    """An immutable TOML fragment delivered by the host, not by model/config input."""

    source: str
    contents: str
    base_dir: Path | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("managed MCP layer requires a source")
        if not isinstance(self.contents, str):
            raise ValueError("managed MCP layer requires TOML text")
        if self.base_dir is not None and (
            not isinstance(self.base_dir, Path) or not self.base_dir.is_absolute()
        ):
            raise ValueError("managed layer base directory must be absolute")


@dataclass(frozen=True, slots=True)
class MCPRequirementsSnapshot:
    """Validated authority and top-level contributors, captured for one Runtime."""

    policy: MCPRequirements
    sources: tuple[tuple[str, tuple[str, ...]], ...] = ()
    execution: tuple[ExecutionRequirementsLayer, ...] = ()
    developer_instructions: ManagedDeveloperInstructions | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.policy, MCPRequirements):
            raise ValueError("managed MCP snapshot requires a validated policy")
        if self.developer_instructions is not None and not isinstance(
            self.developer_instructions, ManagedDeveloperInstructions
        ):
            raise ValueError("managed developer instructions must be host-owned")
        sources = tuple((field, tuple(names)) for field, names in self.sources)
        if len(dict(sources)) != len(sources) or any(
            field not in {"mcp_servers", "plugins"}
            or not names
            or any(not isinstance(name, str) or not name.strip() for name in names)
            for field, names in sources
        ):
            raise ValueError("invalid managed MCP requirement sources")
        object.__setattr__(self, "sources", sources)
        execution = tuple(self.execution)
        if any(not isinstance(layer, ExecutionRequirementsLayer) for layer in execution):
            raise ValueError("managed execution snapshot requires host-owned layers")
        object.__setattr__(self, "execution", execution)
        validate_effective_approval_requirements(execution)


def _merge(base: dict[str, object], overlay: dict[str, object]) -> None:
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = deepcopy(value)


def compose_mcp_requirements(layers: Iterable[MCPRequirementsLayer]) -> MCPRequirementsSnapshot:
    """Compose low-to-high: tables extend recursively; scalar/array values replace."""
    merged: dict[str, object] = {}
    contributors: dict[str, list[str]] = {}
    execution: list[ExecutionRequirementsLayer] = []
    developer_instructions = None
    for layer in layers:
        if not isinstance(layer, MCPRequirementsLayer):
            raise ValueError("managed MCP layers must be host-owned TOML fragments")
        try:
            value = tomllib.loads(layer.contents)
            if "additional_developer_instructions" in value:
                text = value.pop("additional_developer_instructions")
                if not isinstance(text, str):
                    raise ValueError("additional_developer_instructions must be a string")
                developer_instructions = (layer.source, text)
            # Retired official platform settings are not ordinary MCP authority.
            # Preserve the source text but never publish or interpret this tree.
            value.pop("apps", None)
            permission_values = {
                key: value.pop(key)
                for key in (
                    "allowed_sandbox_modes",
                    "allowed_approval_policies",
                    "permissions",
                    "allowed_permission_profiles",
                    "default_permissions",
                    "rules",
                )
                if key in value
            }
            if permission_values:
                execution.append(
                    ExecutionRequirementsLayer(
                        layer.source, json.dumps(permission_values), layer.base_dir
                    )
                )
            validate_requirements_shape(value)
        except ValueError as exc:
            raise ValueError(f"Failed to parse requirements layer {layer.source}: {exc}") from exc
        _merge(merged, value)
        for field in value:
            contributors.setdefault(field, []).append(layer.source)
    sources = tuple(
        (field, tuple(dict.fromkeys(reversed(names))))
        for field, names in sorted(contributors.items())
    )
    try:
        policy = MCPRequirements.from_mapping(merged)
    except ValueError as exc:
        raise ValueError(f"Invalid effective MCP requirements from {sources}: {exc}") from exc
    instructions = (
        ManagedDeveloperInstructions(*developer_instructions)
        if developer_instructions is not None
        else None
    )
    return MCPRequirementsSnapshot(policy, sources, tuple(execution), instructions)


def load_mcp_requirements(
    *,
    system_path: Path | None = None,
    layers: Iterable[MCPRequirementsLayer] = (),
) -> MCPRequirementsSnapshot:
    """Load the trusted system file, then host fragments; only NotFound is absence.

    Path overrides/fragments are embedding-host APIs. No workspace, user settings,
    CORKI_HOME or model metadata can redirect this authority source.
    """
    path = system_requirements_path() if system_path is None else system_path
    if not isinstance(path, Path) or not path.is_absolute():
        raise ValueError("managed system requirements path must be absolute")
    system = ()
    try:
        contents = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        pass
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"Failed to read requirements file {path}: {exc}") from exc
    else:
        system = (MCPRequirementsLayer(str(path), contents, path.parent),)
    return compose_mcp_requirements((*system, *layers))
