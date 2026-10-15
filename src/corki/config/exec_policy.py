"""Immutable host-owned rule text passed to the native execution policy engine."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ExecPolicySource:
    """One captured .rules source, not a rule or prefix supplied by a model."""

    name: str
    contents: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not Path(self.name).is_absolute():
            raise ValueError("exec policy source must have an absolute host path")
        if not isinstance(self.contents, str):
            raise ValueError("exec policy source contents must be text")


@dataclass(frozen=True, slots=True)
class ExecPolicySnapshot:
    """Validated rules and the host inputs that permit child snapshot reuse."""

    config_folders: tuple[Path, ...]
    declared_sources: tuple[ExecPolicySource, ...]
    sources: tuple[ExecPolicySource, ...]
    managed_identity: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if self.managed_identity is not None:
            if isinstance(self.managed_identity, str):
                raise ValueError("managed exec policy identity must be a tuple of native entries")
            object.__setattr__(self, "managed_identity", tuple(self.managed_identity))
            if any(not isinstance(entry, str) for entry in self.managed_identity):
                raise ValueError("managed exec policy identity must contain native text entries")
        for name in ("config_folders", "declared_sources", "sources"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if any(not isinstance(p, Path) or not p.is_absolute() for p in self.config_folders):
            raise ValueError("exec policy config folders must be absolute host paths")
        if any(
            not isinstance(source, ExecPolicySource)
            for source in (*self.declared_sources, *self.sources)
        ):
            raise ValueError("exec policy snapshot requires captured sources")
