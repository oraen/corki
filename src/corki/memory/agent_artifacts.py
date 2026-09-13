"""Validated staged output files, published only under the memory owner's fence."""

import base64
import os
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from uuid import uuid4

from corki.memory import workspace
from corki.memory.artifacts import ensure_memory_layout
from corki.memory.sanitizer import redact_secrets


@dataclass(frozen=True, slots=True)
class AgentArtifacts:
    files: workspace.Snapshot
    removed_summaries: workspace.Snapshot = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SharedAgentArtifacts:
    """The closed worker edited the live memory root; there is nothing to copy back."""


def collect(root: Path, sampled: workspace.Snapshot) -> AgentArtifacts:
    current = workspace.capture(root)
    removed = {
        name: dict(entry)
        for name, entry in sampled.items()
        if _rollout_summary(name) and name not in current
    }
    if {n: e for n, e in current.items() if not workspace.is_output(n)} != {
        n: e for n, e in sampled.items() if not workspace.is_output(n) and n not in removed
    }:
        raise ValueError("consolidation agent changed read-only source inputs")
    files = {name: dict(entry) for name, entry in current.items() if workspace.is_output(name)}
    if "MEMORY.md" not in files or "memory_summary.md" not in files:
        raise ValueError("consolidation agent did not leave required memory artifacts")
    summary = base64.b64decode(files["memory_summary.md"]["content"]).decode("utf-8")
    if not summary.splitlines() or summary.splitlines()[0] != "v1":
        raise ValueError("consolidated memory summary must start with v1")
    for entry in files.values():
        try:
            text = base64.b64decode(entry["content"]).decode("utf-8")
        except UnicodeError:
            continue
        entry["content"] = base64.b64encode(redact_secrets(text).encode()).decode()
    return AgentArtifacts(files, removed)


def _rollout_summary(name: str) -> bool:
    path = PurePosixPath(name)
    return len(path.parts) == 2 and path.parts[0] == "rollout_summaries" and path.suffix == ".md"


def write_file(path: Path, entry: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(base64.b64decode(entry["content"], validate=True))
        temporary.chmod(0o700 if entry["mode"] == "100755" else 0o600)
        os.replace(temporary, path)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


def publish(root: Path, artifacts: AgentArtifacts) -> None:
    ensure_memory_layout(root)
    if workspace.validate(artifacts.files) is None or any(
        not workspace.is_output(name) for name in artifacts.files
    ):
        raise ValueError("invalid staged memory output paths")
    if workspace.validate(artifacts.removed_summaries) is None or any(
        not _rollout_summary(name) for name in artifacts.removed_summaries
    ):
        raise ValueError("invalid staged memory summary deletion paths")
    previous = workspace.capture(root)  # validates existing managed paths before any writes
    for name, expected in artifacts.removed_summaries.items():
        if name in previous and previous[name] != expected:
            raise ValueError(f"rollout summary changed before publication: {name}")
    for name in artifacts.removed_summaries:
        if name in previous:
            (root / name).unlink()
    emptied = set()
    for name in sorted(previous):
        if workspace.is_output(name) and name not in artifacts.files:
            path = root / name
            path.unlink()
            emptied.update(parent for parent in path.parents if root / "skills" in parent.parents)
    # A tool may replace a supporting file with a directory, or vice versa.
    # Never recursively remove hidden/unmanaged contents to make such a write fit.
    emptied.update(root / name for name in artifacts.files if (root / name).is_dir())
    for directory in sorted(emptied, key=lambda p: len(p.parts), reverse=True):
        with suppress(OSError):
            directory.rmdir()
    for name in sorted(artifacts.files, key=lambda n: (n == "memory_summary.md", n)):
        write_file(root / name, artifacts.files[name])
