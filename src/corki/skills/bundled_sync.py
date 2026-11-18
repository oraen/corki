"""Preserve local bundled-skill edits, following Hermes skills_sync._update_existing_skill."""

import hashlib
import json
import logging
import os
import shutil
from pathlib import Path

from corki.platforms.paths import is_link_path

MANIFEST = ".corki-system-skills.manifest.json"


def digest(node):
    result = hashlib.sha256()
    if isinstance(node, Path) and is_link_path(node):
        result.update(b"symlink:" + os.fsencode(os.readlink(node)))
    elif node.is_dir():
        for child in sorted(node.iterdir(), key=lambda p: p.name):
            if child.name in {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}:
                continue
            result.update(child.name.encode())
            result.update(digest(child).encode())
    else:
        result.update(node.read_bytes())
    return result.hexdigest()


def baseline(source, exclude=()):
    return {node.name: digest(node) for node in source.iterdir() if node.name not in exclude}


def manifest(directory):
    path = directory / MANIFEST
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        # Hermes skills_sync: unknown provenance establishes a baseline, never overwrites edits.
        logging.getLogger(__name__).warning("Bundled skill manifest unavailable; preserving copies")
        return {}


def save(directory, value):
    path = directory / MANIFEST
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def merge(source, destination: Path, staging: Path, *, exclude=()):
    incoming, origins = baseline(source, exclude), manifest(destination)
    result = dict(origins)

    def keep(name):
        old, target = destination / name, staging / name
        if is_link_path(target):
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        if is_link_path(old):
            shutil.copy2(old, target, follow_symlinks=False)
        elif old.is_dir():
            shutil.copytree(old, target, symlinks=True)
        else:
            shutil.copy2(old, target, follow_symlinks=False)

    for name, current_hash in incoming.items():
        current = destination / name
        if name in origins:
            if not current.exists():
                # Hermes keeps an intentional local deletion; don't re-seed it on upgrade.
                target = staging / name
                if target.is_dir():
                    shutil.rmtree(target)
                elif target.exists():
                    target.unlink()
            elif origins[name] == current_hash or digest(current) != origins[name]:
                keep(name)
            else:
                result[name] = current_hash
        elif current.exists():
            # Pre-manifest upgrade: unknown origin, establish the current copy as baseline.
            keep(name)
            result[name] = digest(current)
        else:
            result[name] = current_hash
    if destination.is_dir():
        for node in destination.iterdir():
            if (
                node.name not in incoming
                and node.name not in exclude
                and node.name
                not in {
                    MANIFEST,
                    ".corki-system-skills.marker",
                }
            ):
                keep(node.name)
    save(staging, result)
