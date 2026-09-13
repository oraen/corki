"""Filesystem-only project discovery, also run inside the fixed sandbox helper."""

import logging
import stat
from dataclasses import dataclass
from pathlib import Path

from corki.config.instructions import ProjectInstructionsConfig

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProjectInstruction:
    text: str
    source: Path


def discover_root(cwd: Path, markers: tuple[str, ...]) -> Path:
    """Find the nearest marker using logical ancestry, ignoring marker probe errors."""
    cwd = cwd.absolute()
    for directory in (cwd, *cwd.parents):
        for marker in markers:
            try:
                (directory / marker).stat()
            except OSError:
                continue
            return directory
    return cwd


def load_project_entries(
    cwd: Path, config: ProjectInstructionsConfig
) -> tuple[ProjectInstruction, ...]:
    """Read raw root-to-cwd bytes; empty overrides mask defaults only for projects."""
    if not config.max_bytes or config.trust_level == "untrusted":
        return ()
    cwd = cwd.absolute()
    root = discover_root(cwd, config.root_markers)
    directories = [cwd]
    while directories[-1] != root:
        directories.append(directories[-1].parent)
    names = tuple(
        dict.fromkeys(("AGENTS.override.md", "AGENTS.md", *filter(None, config.fallback_filenames)))
    )
    # Discovery is a separate phase: a later metadata denial must not be
    # hidden merely because an earlier file fills the text budget.
    paths = []
    for directory in reversed(directories):
        for name in names:
            path = directory / name
            try:
                if stat.S_ISREG(path.stat().st_mode):
                    paths.append(path)
                    break
            except FileNotFoundError:
                continue
    remaining, entries = config.max_bytes, []
    for selected in paths:
        if remaining == 0:
            break
        try:
            raw = selected.read_bytes()
        except FileNotFoundError:
            continue
        if len(raw) > remaining:
            _LOG.warning(
                "project doc exceeds remaining budget; truncating %s to %s bytes",
                selected,
                remaining,
            )
            raw = raw[:remaining]
        text = raw.decode("utf-8", "replace")
        if text.strip():
            entries.append(ProjectInstruction(text, selected))
            remaining -= len(raw)
    return tuple(entries)
