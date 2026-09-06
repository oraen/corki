"""Hierarchical project-instruction discovery compatible with Codex semantics."""

from __future__ import annotations

from pathlib import Path


def load_project_instructions(root: Path, cwd: Path, *, byte_budget: int = 32_768) -> str:
    """Read applicable ``AGENTS.md`` files from project root toward ``cwd``."""

    root = root.resolve()
    cwd = cwd.resolve()
    try:
        relative = cwd.relative_to(root)
    except ValueError:
        return ""
    directories = [root]
    current = root
    for part in relative.parts:
        current /= part
        directories.append(current)

    sections: list[str] = []
    remaining = max(0, byte_budget)
    for directory in directories:
        if remaining == 0:
            break
        # Like Codex, a directory-local override takes precedence but does not
        # suppress instructions inherited from ancestor directories.
        path = next(
            (
                candidate
                for name in ("AGENTS.override.md", "AGENTS.md")
                if (candidate := directory / name).is_file()
            ),
            None,
        )
        if path is None:
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        selected = raw[:remaining]
        remaining -= len(selected)
        # Codex accepts a damaged project instruction file with replacement
        # characters instead of silently discarding every valid byte around it.
        content = selected.decode("utf-8", errors="replace")
        sections.append(f"## {path}\n\n{content.strip()}")
    return "\n\n".join(sections)
