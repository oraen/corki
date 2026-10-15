"""Filesystem artifacts used by the long-term memory read and write paths."""

from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from corki.memory import workspace
from corki.memory.extension_retention import prune_old_extension_resources
from corki.memory.models import ConsolidatedMemory, StageOneMemory
from corki.memory.workspace import workspace_files as _workspace_files

_SAFE_SLUG = re.compile(r"[^a-z0-9]+")


def ensure_memory_layout(root: Path) -> None:
    """Create the private memory tree and reject a symlinked store root."""

    if root.is_symlink():
        raise ValueError(f"memory root cannot be a symbolic link: {root}")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink():
        raise ValueError(f"memory root cannot be a symbolic link: {root}")
    root.chmod(0o700)
    # Validate every managed path component separately.  Checking only the
    # leaf would miss a symlinked ``extensions`` ancestor and could redirect
    # model-generated artifacts outside Corki's private store.
    for directory in (
        root / "rollout_summaries",
        root / "skills",
        root / "extensions",
        root / "extensions" / "ad_hoc",
        root / "extensions" / "ad_hoc" / "notes",
    ):
        if directory.is_symlink():
            raise ValueError(f"managed memory directory cannot be a symbolic link: {directory}")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError(f"managed memory path must be a regular directory: {directory}")


def sync_stage_one_artifacts(root: Path, memories: tuple[StageOneMemory, ...]) -> bool:
    """Synchronize deterministic raw-memory and rollout-summary inputs.

    Returns whether the filesystem inputs changed.  Consolidation can skip a
    model call when a retried job observes an already-current workspace.
    """

    ensure_memory_layout(root)
    desired: dict[Path, str] = {}
    desired[root / "raw_memories.md"] = _raw_memories(memories)
    summary_dir = root / "rollout_summaries"
    for memory in memories:
        desired[summary_dir / f"{rollout_summary_stem(memory)}.md"] = _rollout_summary(memory)

    changed = False
    keep = {path.name for path in desired if path.parent == summary_dir}
    for path in summary_dir.iterdir():
        if path.is_symlink() or not path.is_file() or path.suffix != ".md":
            continue
        if path.name not in keep:
            path.unlink()
            changed = True
    for path, content in desired.items():
        if _read_text(path) != content:
            _atomic_write(path, content)
            changed = True
    # Expiry is an input change even without a new DB watermark. Run under the
    # caller's existing lease fence, before capturing the consolidation baseline.
    expired = prune_old_extension_resources(root)
    return changed or expired


def write_consolidated_artifacts(root: Path, value: ConsolidatedMemory) -> None:
    """Replace each validated artifact atomically, not the entire file set.

    The caller must fence concurrent consolidators. An I/O failure between
    replacements can still leave mixed generations; this is not a transaction.
    """

    ensure_memory_layout(root)
    for skill in value.skills:
        target = root / "skills" / skill.name
        if target.is_symlink() or (target.exists() and not target.is_dir()):
            raise ValueError(f"memory skill path must be a regular directory: {target}")
    memory = _versioned(value.memory)
    summary = _versioned(value.summary)
    # Write detail before the routing summary. Readers may see a mixed pair;
    # callers must not describe this per-file replacement as atomic publication.
    _atomic_write(root / "MEMORY.md", memory)
    _atomic_write(root / "memory_summary.md", summary)
    _sync_memory_skills(root, value)


def stage_one_digest(root: Path) -> str:
    """Hash input files separately so mid-sampling changes remain pending."""
    return workspace.digest(workspace.capture(root), outputs=False)


def baseline_matches(root: Path, input_digest: str) -> bool:
    """Skip only if both input and validated output snapshots are unchanged."""
    baseline_path = root / ".consolidation-baseline.json"
    if baseline_path.is_symlink():
        raise ValueError("memory baseline cannot be a symbolic link")
    try:
        value = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return False
    if (
        not isinstance(value, dict)
        or value.get("version") != 3
        or value.get("stage_one_sha256") != input_digest
    ):
        # Legacy baselines have no content evidence. Rebuild them
        # through a normal successful consolidation, not a silent migration.
        return False
    try:
        validate_consolidation_artifacts(root)
    except (OSError, UnicodeError, ValueError):
        return False
    snapshot = workspace.validate(value.get("workspace"))
    return (
        snapshot is not None
        and snapshot == workspace.capture(root)
        and value.get("output_sha256") == _output_digest(root)
    )


def write_baseline(root: Path, digest: str, *, sampled: workspace.Snapshot | None = None) -> None:
    validate_consolidation_artifacts(root)
    current = workspace.capture(root)
    snapshot = (
        current
        if sampled is None
        else {
            **{name: entry for name, entry in sampled.items() if not workspace.is_output(name)},
            **{name: entry for name, entry in current.items() if workspace.is_output(name)},
        }
    )
    _atomic_write(
        root / ".consolidation-baseline.json",
        json.dumps(
            {
                "version": 3,
                "stage_one_sha256": digest,
                "output_sha256": workspace.digest(current, outputs=True),
                "workspace": snapshot,
            },
            sort_keys=True,
        )
        + "\n",
    )


def validate_consolidation_artifacts(root: Path) -> None:
    _workspace_files(root)  # reject links/non-regular files without traversing their targets
    _validate_memory_headers(root)


def _validate_memory_headers(root: Path) -> None:
    memory = root / "MEMORY.md"
    if not memory.is_file():
        raise ValueError("consolidated MEMORY.md must be a regular file")
    summary = (root / "memory_summary.md").read_text(encoding="utf-8")
    if not summary.splitlines() or summary.splitlines()[0] != "v1":
        raise ValueError("consolidated memory summary must start with v1")


def remove_memory_symlinks(root: Path) -> int:
    """Unlink workspace links without traversing their targets, including hidden entries."""
    if root.is_symlink():
        raise ValueError("memory root cannot be a symbolic link")
    pending, removed = [root], 0
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                path = Path(entry.path)
                if entry.is_symlink():
                    if os.name == "nt" and entry.is_dir():
                        path.rmdir()
                    else:
                        path.unlink()
                    removed += 1
                elif entry.is_dir(follow_symlinks=False):
                    pending.append(path)
    return removed


def validate_shared_artifacts(root: Path) -> None:
    """Match phase2's post-close validation without inventing a copy-back allowlist."""
    if remove_memory_symlinks(root):
        raise ValueError("removed symbolic links from consolidated memory workspace")
    _validate_memory_headers(root)


def remove_workspace_diff(root: Path) -> None:
    """Remove only the generated prompt artifact, never the shared memory tree."""
    with suppress(FileNotFoundError):
        (root / "phase2_workspace_diff.md").unlink()


def write_workspace_diff(root: Path, content: str) -> None:
    """Place the generated diff in the actual worker directory."""
    _atomic_write(root / "phase2_workspace_diff.md", content)


def read_skill_artifacts(root: Path) -> dict[str, str]:
    """Give consolidation the previous procedures, not just their existence."""
    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in _workspace_files(root)
        if path.relative_to(root).parts[0] == "skills" and path.suffix == ".md"
    }


def _output_digest(root: Path) -> str:
    return workspace.digest(workspace.capture(root), outputs=True)


def read_optional(path: Path) -> str:
    return _read_text(path) or ""


def rollout_summary_stem(memory: StageOneMemory) -> str:
    """Build a stable, grep-friendly filename without trusting model slugs."""

    try:
        value = UUID(str(memory.thread_id))
        short_hash = hashlib.sha256(value.bytes).hexdigest()[:6]
    except ValueError:
        short_hash = hashlib.sha256(str(memory.thread_id).encode()).hexdigest()[:6]
    try:
        timestamp = datetime.fromisoformat(memory.source_updated_at.replace("Z", "+00:00"))
    except ValueError:
        timestamp = datetime.now(UTC)
    prefix = f"{timestamp.strftime('%Y-%m-%dT%H-%M-%S')}-{short_hash}"
    if not memory.rollout_slug:
        return prefix
    slug = _SAFE_SLUG.sub("_", memory.rollout_slug.lower()).strip("_")[:60].rstrip("_")
    return f"{prefix}-{slug}" if slug else prefix


def _raw_memories(memories: tuple[StageOneMemory, ...]) -> str:
    lines = ["# Raw Memories", ""]
    if not memories:
        return "# Raw Memories\n\nNo raw memories yet.\n"
    lines.extend(["Merged stage-one memories in stable thread-id order:", ""])
    for memory in sorted(memories, key=lambda item: str(item.thread_id)):
        lines.extend(
            [
                f"## Thread `{memory.thread_id}`",
                f"updated_at: {memory.source_updated_at}",
                f"cwd: {memory.cwd}",
                f"rollout_summary_file: {rollout_summary_stem(memory)}.md",
                "",
                memory.raw_memory.strip(),
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _rollout_summary(memory: StageOneMemory) -> str:
    lines = [
        f"thread_id: {memory.thread_id}",
        f"updated_at: {memory.source_updated_at}",
        f"cwd: {memory.cwd}",
        "",
        memory.rollout_summary.strip(),
    ]
    return "\n".join(lines).rstrip() + "\n"


def _versioned(value: str) -> str:
    body = value.strip()
    if not body:
        raise ValueError("consolidated memory artifact must not be empty")
    return (body if body.splitlines()[0].strip() == "v1" else f"v1\n\n{body}") + "\n"


def _sync_memory_skills(root: Path, value: ConsolidatedMemory) -> None:
    skills_root = root / "skills"
    keep = {skill.name for skill in value.skills}
    for path in skills_root.iterdir():
        if path.is_symlink() or not path.is_dir() or path.name in keep:
            continue
        # This directory is exclusively generated by the memory consolidator.
        for child in path.iterdir():
            if child.is_file() and not child.is_symlink():
                child.unlink()
        # Unknown nested content belongs to a future format; preserve it.
        with suppress(OSError):
            path.rmdir()
    for skill in value.skills:
        directory = skills_root / skill.name
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        body = (
            "---\n"
            f"name: {skill.name}\n"
            f"description: {json.dumps(skill.description, ensure_ascii=False)}\n"
            "---\n\n"
            f"{skill.content.strip()}\n"
        )
        _atomic_write(directory / "SKILL.md", body)


def _read_text(path: Path) -> str | None:
    if path.is_symlink():
        raise ValueError(f"memory artifact cannot be a symbolic link: {path}")
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()
