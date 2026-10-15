"""Source-mapped soft-cap pruning for resumable unified-exec sessions."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from corki.tools.builtin.process import _ProcessSession

MAX_PROCESSES = 64
RECENT_PROTECTED = 8


def _exited(session: _ProcessSession) -> bool:
    # Codex ProcessState.failed sets has_exited before OS termination is confirmed.
    return session.process.returncode is not None or session.failure is not None


def select_prunable(sessions: Iterable[_ProcessSession]) -> _ProcessSession | None:
    remaining = list(sessions)
    if len(remaining) < MAX_PROCESSES:
        return None
    locked_exited = False
    while remaining:
        recent = sorted(remaining, key=lambda session: session.last_used, reverse=True)
        protected = {session.id for session in recent[:RECENT_PROTECTED]}
        lru = sorted(
            (session for session in remaining if session.id not in protected),
            key=lambda session: session.last_used,
        )
        if not lru:
            return None
        candidate = next((s for s in lru if _exited(s)), lru[0])
        exited = _exited(candidate)
        if locked_exited and not exited:
            return None
        if not candidate.interaction_lock.locked():
            return candidate
        locked_exited |= exited
        remaining.remove(candidate)
    return None
