"""Local skill identity and install checks shared by installer commands."""
from __future__ import annotations

import os
import re
from pathlib import Path


def corki_home() -> Path:
    return Path(os.environ.get("CORKI_HOME") or "~/.corki").expanduser()


def skill_roots() -> list[tuple[str, Path]]:
    return [("corki", corki_home() / "skills"),
            ("public", Path.home() / ".agents/skills"),
            ("builtin", corki_home() / "skills/.system")]


def read_metadata(path: Path, *, required_name: bool = False) -> tuple[str, str]:
    import yaml
    from corki.skills.frontmatter import parse
    if path.stat().st_size > 128 * 1024:
        raise ValueError("SKILL.md exceeds the runtime limit of 128 KB")
    try:
        name, description, _ = parse(path.read_text(encoding="utf-8-sig"), "" if required_name else path.parent.name)
    except yaml.YAMLError as error:
        raise ValueError(f"Invalid SKILL.md metadata: {error}") from error
    return name, description


def local_skills(extra_root: Path | None = None) -> list[dict]:
    roots = skill_roots()
    if extra_root is not None:
        roots.append(("custom", extra_root))
    seen: set[Path] = set()
    result = []
    for scope, root in roots:
        if not root.is_dir():
            continue
        for directory, children, files in os.walk(root, followlinks=False):
            children[:] = sorted(n for n in children if not n.startswith('.')
                                 and n != '__pycache__' and not (Path(directory) / n).is_symlink())
            path = Path(directory) / 'SKILL.md'
            if 'SKILL.md' not in files or path.is_symlink() or path.resolve() in seen:
                continue
            seen.add(path.resolve())
            try:
                name, description = read_metadata(path)
            except (OSError, ValueError) as error:
                result.append(dict(name=path.parent.name, path=str(path), scope=scope,
                                   valid=False, error=str(error)))
            else:
                result.append(dict(name=name, description=description, path=str(path), scope=scope, valid=True))
    return sorted(result, key=lambda row: (row['name'].casefold(), row['path']))


def validate_install_name(name: str) -> None:
    # Keep the same filesystem-safe naming accepted by the manager importer.
    if not re.fullmatch(r"[\w-][\w.\-]{0,79}", name) or name.endswith('.'):
        raise ValueError("Invalid skill name: use a single safe directory name")
    if re.match(r"^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", name, re.I):
        raise ValueError("Skill name is reserved on Windows")
