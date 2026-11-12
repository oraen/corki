"""Install bundled skills into Corki's private system-skill cache."""

from __future__ import annotations

import hashlib
import os
import shutil
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from tempfile import mkdtemp

from corki.storage.file_lock import lock, open_lock, unlock_and_close

_MARKER = ".corki-system-skills.marker"
_WORK_BUNDLED = frozenset({"corki-text-C&E"})
_WORK_MARKER = ".corki-work-skills.marker"


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
    skills_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    owner = open_lock(skills_root / ".system.lock")
    try:
        lock(owner)
    except BaseException:
        owner.close()
        raise
    try:
        return _install_locked(skills_root, source)
    finally:
        unlock_and_close(owner)


def _install_locked(skills_root: Path, source: Traversable) -> Path:
    # Recheck the marker under the same OS lock as backup/replacement. Otherwise
    # concurrent CLI, manager and desktop starts can remove one another's backup.
    _seed_work_skills(skills_root, source)
    destination = skills_root / ".system"
    fingerprint = _fingerprint(source)
    marker = destination / _MARKER
    try:
        if destination.is_dir() and marker.read_text(encoding="utf-8").strip() == fingerprint:
            return destination
    except OSError:
        pass

    staging = Path(mkdtemp(prefix=".system-", dir=skills_root))
    try:
        _copy_tree(source, staging, exclude=_WORK_BUNDLED)
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


def _copy_tree(source: Traversable, destination: Path, *, exclude=()) -> None:
    for child in source.iterdir():
        if child.name in exclude:
            continue
        target = destination / child.name
        if child.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            _copy_tree(child, target)
        elif child.is_file():
            target.write_bytes(child.read_bytes())


def _fingerprint(source: Traversable) -> str:
    digest = hashlib.sha256(b"corki-system-skills-v2\0")

    def visit(node: Traversable, prefix: str = "") -> None:
        for child in sorted(node.iterdir(), key=lambda item: item.name):
            if not prefix and child.name in _WORK_BUNDLED:
                continue
            relative = f"{prefix}{child.name}"
            digest.update(relative.encode())
            digest.update(b"\0")
            if child.is_dir():
                visit(child, f"{relative}/")
            elif child.is_file():
                digest.update(child.read_bytes())

    visit(source)
    return digest.hexdigest()


def _seed_work_skills(skills_root: Path, source: Traversable) -> None:
    """Migrate legacy copies once; preserve user edits and intentional deletion."""
    candidates = [name for name in sorted(_WORK_BUNDLED) if source.joinpath(name).is_dir()]
    if not candidates:
        return
    marker = skills_root / _WORK_MARKER
    try:
        installed = set(marker.read_text(encoding="utf-8").splitlines())
    except FileNotFoundError:
        installed = set()
    if all(name in installed for name in candidates):
        return
    for name in candidates:
        if name in installed:
            continue
        work = skills_root / "work"
        work.mkdir(mode=0o700, exist_ok=True)
        target = work / name
        if not target.exists() and not target.is_symlink():
            legacy = skills_root / ".system" / name
            origin = legacy if legacy.is_dir() else source.joinpath(name)
            staging = Path(mkdtemp(prefix=".seed-", dir=work))
            try:
                _copy_tree(origin, staging)
                os.replace(staging, target)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
        installed.add(name)
    staging = skills_root / (_WORK_MARKER + ".tmp")
    staging.write_text("\n".join(sorted(installed)) + "\n", encoding="utf-8")
    os.replace(staging, marker)
