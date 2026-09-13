"""Immutable shell environment policy; distinct from MCP server environment rules."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class ShellEnvironmentPolicy:
    """Validated host configuration, never supplied by a model tool argument."""

    inherit: str = "all"
    ignore_default_excludes: bool = True
    exclude: tuple[str, ...] = ()
    set: Mapping[str, str] = field(default_factory=dict, repr=False)
    include_only: tuple[str, ...] = ()
    # The pinned Codex parses this field but does not read it during execution.
    use_profile: bool = False

    def __post_init__(self) -> None:
        if self.inherit not in ("all", "core", "none"):
            raise ValueError("shell_environment_policy.inherit must be all, core or none")
        for name in ("ignore_default_excludes", "use_profile"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"shell_environment_policy.{name} must be a boolean")
        for name in ("exclude", "include_only"):
            value = getattr(self, name)
            if not isinstance(value, (list, tuple)) or not all(isinstance(p, str) for p in value):
                raise ValueError(f"shell_environment_policy.{name} must be an array of strings")
            object.__setattr__(self, name, tuple(value))
        if not isinstance(self.set, Mapping) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in self.set.items()
        ):
            raise ValueError("shell_environment_policy.set must be a table of strings")
        object.__setattr__(self, "set", MappingProxyType(dict(self.set)))


def parse_shell_environment_policy(value: object) -> ShellEnvironmentPolicy:
    """Accept canonical keyed filters or legacy arrays, never a mixture of both."""
    if not isinstance(value, Mapping):
        raise ValueError("shell_environment_policy must be a TOML table")
    exclude = value.get("exclude", ())
    include_only = value.get("include_only", ())
    if "filters" in value:
        if "exclude" in value or "include_only" in value:
            raise ValueError("cannot mix `filters` with legacy `exclude` or `include_only`")
        filters = value["filters"]
        if not isinstance(filters, Mapping) or not all(
            isinstance(k, str) and v in ("include", "exclude") for k, v in filters.items()
        ):
            raise ValueError(
                "shell_environment_policy.filters must map patterns to include/exclude"
            )
        if len({k.lower() for k in filters}) != len(filters):
            raise ValueError("duplicate shell environment filter ignoring case")
        exclude = tuple(k for k in sorted(filters) if filters[k] == "exclude")
        include_only = tuple(k for k in sorted(filters) if filters[k] == "include")
    return ShellEnvironmentPolicy(
        inherit=value.get("inherit", "all"),
        ignore_default_excludes=value.get("ignore_default_excludes", True),
        exclude=exclude,
        set=value.get("set", {}),
        include_only=include_only,
        use_profile=value.get("experimental_use_profile", False),
    )
