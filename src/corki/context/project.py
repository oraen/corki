"""Build a bounded snapshot of the current coding workspace."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

_GIT_TIMEOUT_SECONDS = 2.0
_GIT_TERMINATE_GRACE_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class ProjectSnapshot:
    root: Path
    git_branch: str
    git_status: str


def discover_project_root(cwd: Path) -> Path:
    """Return the nearest ancestor containing a project or VCS marker."""

    markers = (".git", "pyproject.toml", "package.json", "Cargo.toml", "go.mod")
    current = cwd.resolve()
    for candidate in (current, *current.parents):
        if any((candidate / marker).exists() for marker in markers):
            return candidate
    return current


async def inspect_project(cwd: Path) -> ProjectSnapshot:
    root = discover_project_root(cwd)
    branch, status = await asyncio.gather(
        _git(root, "branch", "--show-current"),
        _git(root, "status", "--short"),
    )
    if branch is None:
        return ProjectSnapshot(root=root, git_branch="not a git repository", git_status="unknown")
    return ProjectSnapshot(
        root=root,
        git_branch=branch or "detached HEAD",
        git_status="clean" if not status else "dirty\n" + status,
    )


async def _git(cwd: Path, *arguments: str) -> str | None:
    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            *arguments,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=_GIT_TIMEOUT_SECONDS)
    except TimeoutError:
        # ``wait_for`` cancels ``communicate`` but does not terminate the child.
        # Corki is a long-lived CLI, so even this best-effort context probe must
        # not leave a stuck git process behind on every turn.
        if process is not None:
            await _terminate(process)
        return None
    except asyncio.CancelledError:
        # Match Codex's kill-on-drop process discipline when a turn or the CLI
        # is cancelled while the project snapshot is being collected.
        if process is not None:
            await _terminate(process)
        raise
    except OSError:
        return None
    if process.returncode != 0:
        return None
    return stdout.decode("utf-8", errors="replace").strip()


async def _terminate(process: asyncio.subprocess.Process) -> None:
    """Reap a probe subprocess, escalating to kill after a short grace period."""

    if process.returncode is not None:
        await process.wait()
        return
    with suppress(ProcessLookupError):
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=_GIT_TERMINATE_GRACE_SECONDS)
    except TimeoutError:
        with suppress(ProcessLookupError):
            process.kill()
        await process.wait()
