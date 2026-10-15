"""Agent Plugin host paths: resolve existing symlinks before lexical containment."""

import os
import re
from pathlib import Path

_PLACEHOLDER = re.compile(r"\$\{PLUGIN_(ROOT|DATA)\}")


def expand_paths(value: str, root: Path, data: Path) -> str:
    """Substitute once; inserted host paths are never interpreted as templates."""
    return _PLACEHOLDER.sub(lambda m: str(root if m[1] == "ROOT" else data), value)


def resolve_prefix(path: Path) -> Path:
    """Permit missing descendants, but not dangling or otherwise invalid symlinks."""
    missing = []
    existing = path
    while True:
        try:
            resolved = existing.resolve(strict=True)
        except FileNotFoundError:
            if existing.is_symlink() or existing.parent == existing:
                raise ValueError(f"failed to resolve symlinked path: {path}") from None
            missing.append(existing.name)
            existing = existing.parent
        else:
            return Path(os.path.normpath(resolved.joinpath(*reversed(missing))))


def contained_path(value: str, root: Path) -> Path:
    """Canonicalize before checking the selected package or data root boundary."""
    path = resolve_prefix(root / value)
    if not path.is_relative_to(root):
        raise ValueError(f"expanded path must remain within {root}")
    return path


def working_directory(value: str, root: Path, data: Path) -> Path:
    """Only portable package/data-relative cwd expressions carry path authority."""
    if value.startswith("./") and "\\" not in value:
        allowed = root
    else:
        allowed = None
        for prefix, base in (("${PLUGIN_ROOT}", root), ("${PLUGIN_DATA}", data)):
            if value == prefix or (value.startswith(prefix + "/") and "\\" not in value):
                allowed = base
                break
        if allowed is None:
            raise ValueError(
                "Agent Plugin cwd must be a contained ./, PLUGIN_ROOT or PLUGIN_DATA path"
            )
    return contained_path(expand_paths(value, root, data), allowed)
