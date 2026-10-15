"""Install bundled skills into Corki's private system-skill cache."""

from __future__ import annotations

import hashlib
import os
import shutil
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from tempfile import mkdtemp

_MARKER = ".corki-system-skills.marker"


def bundled_skills_source() -> Traversable:
    """Return packaged assets, falling back to the source tree in editable installs."""

    packaged = resources.files("corki").joinpath("_builtin_skills")
    if packaged.is_dir():
        return packaged
    source = Path(__file__).resolve().parents[3] / "skills"
    if source.is_dir():
        return source
    raise FileNotFoundError("Corki bundled skills are missing from this installation")


def install_bundled_skills(home: Path, source: Traversable | None = None) -> Path:
    """Atomically refresh ``~/.corki/skills/.system`` when assets change."""

    source = source or bundled_skills_source()
    skills_root = home / "skills"
    destination = skills_root / ".system"
    skills_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    fingerprint = _fingerprint(source)
    marker = destination / _MARKER
    try:
        if destination.is_dir() and marker.read_text(encoding="utf-8").strip() == fingerprint:
            return destination
    except OSError:
        pass

    staging = Path(mkdtemp(prefix=".system-", dir=skills_root))
    try:
        _copy_tree(source, staging)
        (staging / _MARKER).write_text(f"{fingerprint}\n", encoding="utf-8")
        backup = skills_root / ".system.previous"
        if backup.exists():
            shutil.rmtree(backup)
        if destination.exists():
            os.replace(destination, backup)
        try:
            os.replace(staging, destination)
        except BaseException:
            if backup.exists() and not destination.exists():
                os.replace(backup, destination)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return destination


def _copy_tree(source: Traversable, destination: Path) -> None:
    for child in source.iterdir():
        target = destination / child.name
        if child.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            _copy_tree(child, target)
        elif child.is_file():
            target.write_bytes(child.read_bytes())


def _fingerprint(source: Traversable) -> str:
    digest = hashlib.sha256(b"corki-system-skills-v1\0")

    def visit(node: Traversable, prefix: str = "") -> None:
        for child in sorted(node.iterdir(), key=lambda item: item.name):
            relative = f"{prefix}{child.name}"
            digest.update(relative.encode())
            digest.update(b"\0")
            if child.is_dir():
                visit(child, f"{relative}/")
            elif child.is_file():
                digest.update(child.read_bytes())

    visit(source)
    return digest.hexdigest()
