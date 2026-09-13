"""Read configured native installations; directory presence is not enablement."""

import os
import re
import tomllib
from dataclasses import dataclass
from functools import cmp_to_key
from hashlib import sha256
from pathlib import Path

from corki.config.layers import LocalConfigState, _merge
from corki.config.plugin_policies import raw_plugin_policies

_VERSION = re.compile(r"[A-Za-z0-9_+.-]+")
_SEMVER = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
)


@dataclass(frozen=True, slots=True)
class PluginId:
    name: str
    marketplace: str

    def __post_init__(self):
        if (
            not isinstance(self.name, str)
            or re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*", self.name) is None
            or not isinstance(self.marketplace, str)
            or re.fullmatch(r"[A-Za-z0-9_-]+", self.marketplace) is None
        ):
            raise ValueError("invalid plugin ID path segments")

    @classmethod
    def parse(cls, value: str) -> "PluginId":
        name, separator, marketplace = value.rpartition("@")
        if not separator:
            raise ValueError("expected <plugin>@<marketplace>")
        return cls(name, marketplace)

    @property
    def key(self):
        return f"{self.name}@{self.marketplace}"

    def data_root(self, home: Path, *, agent_plugin: bool) -> Path:
        if agent_plugin:
            digest = sha256((self.marketplace + "\0" + self.name).encode()).hexdigest()
            return home / "plugins/data/agent-plugins" / digest
        return home / "plugins/data" / f"{self.name}-{self.marketplace}"


@dataclass(frozen=True, slots=True)
class PluginInstallation:
    identity: str
    root: Path
    enabled: bool
    plugin_id: PluginId | None = None
    version: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class PluginStoreSource:
    """Captured host home/config, expanded inside the existing discovery worker."""

    home: Path
    configuration: LocalConfigState

    def __post_init__(self):
        if not isinstance(self.home, Path) or not self.home.is_absolute():
            raise ValueError("plugin store requires an absolute host home")
        if not isinstance(self.configuration, LocalConfigState):
            raise ValueError("plugin store requires captured host config layers")

    def installations(self) -> tuple[PluginInstallation, ...]:
        document = {}
        for layer in self.configuration.layers:
            if layer.disabled_reason is None:
                values = tomllib.loads(layer.contents)
                if "plugins" in values:
                    _merge(document, {"plugins": values["plugins"]})
        cache = self.home / "plugins/cache"
        result = []
        for identity, policy in sorted(raw_plugin_policies(document).items()):
            enabled = policy.get("enabled", True)
            try:
                plugin_id = PluginId.parse(identity)
            except ValueError as error:
                result.append(
                    PluginInstallation(
                        identity, cache, enabled, error=str(error) if enabled else None
                    )
                )
                continue
            base = cache / plugin_id.marketplace / plugin_id.name
            version = active_version(base)
            result.append(
                PluginInstallation(
                    identity,
                    base / version if version is not None else base,
                    enabled,
                    plugin_id,
                    version,
                    "plugin is not installed" if enabled and version is None else None,
                )
            )
        return tuple(result)


def active_version(base: Path) -> str | None:
    """Match native directory-type filtering and local-first active version selection."""
    versions = []
    try:
        with os.scandir(base) as entries:
            for entry in entries:
                try:
                    if (
                        entry.is_dir(follow_symlinks=False)
                        and entry.name not in {".", ".."}
                        and _VERSION.fullmatch(entry.name) is not None
                    ):
                        versions.append(entry.name)
                except OSError:
                    continue
    except OSError:
        return None
    if "local" in versions:
        return "local"
    return sorted(versions, key=cmp_to_key(compare_versions))[-1] if versions else None


def compare_versions(left: str, right: str) -> int:
    """Rust semver Version Ord includes build metadata; non-SemVer pairs are lexical."""
    left_key, right_key = _semver_key(left), _semver_key(right)
    a, b = (
        (left_key, right_key) if left_key is not None and right_key is not None else (left, right)
    )
    return (a > b) - (a < b)


def _semver_key(value):
    match = _SEMVER.fullmatch(value)
    if match is None:
        return None
    major, minor, patch, pre, build = match.groups()
    if any(len(n) > 20 or int(n) >= 2**64 for n in (major, minor, patch)):
        return None
    if pre and any(p.isdigit() and len(p) > 1 and p.startswith("0") for p in pre.split(".")):
        return None

    def identifiers(text):
        # Numeric components can exceed u64; compare lengths/bytes without bigint parsing.
        return tuple(
            (0, len(part.lstrip("0")), part.lstrip("0"), len(part)) if part.isdigit() else (1, part)
            for part in text.split(".")
        )

    return (
        int(major),
        int(minor),
        int(patch),
        (0, identifiers(pre)) if pre else (1,),
        identifiers(build) if build else (),
    )
