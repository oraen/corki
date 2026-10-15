"""Transient hook lifecycle; persistent output belongs to the transcript renderer."""

from dataclasses import dataclass
from typing import Literal

from corki.protocol.events import HookRunSummary


@dataclass
class _Run:
    message: str | None
    state: Literal["pending", "visible", "linger"]
    time: float


class HookActivity:
    """Caller-owned monotonic clock and timer, with no background tasks."""

    def __init__(self) -> None:
        self._runs: dict[str, _Run] = {}

    def start(self, run: HookRunSummary, now: float) -> None:
        self._runs[run.id] = _Run(run.status_message, "pending", now + 0.3)

    def complete(self, run: HookRunSummary, now: float) -> None:
        previous = self._runs.pop(run.id, None)
        quiet = run.status == "completed" and all(e.kind == "context" for e in run.entries)
        if quiet and previous is not None and previous.state == "visible":
            deadline = previous.time + 0.6
            if now < deadline:
                self._runs[run.id] = _Run(previous.message, "linger", deadline)

    def advance(self, now: float) -> None:
        for key, run in tuple(self._runs.items()):
            if run.state == "pending" and now >= run.time:
                run.state, run.time = "visible", now
            elif run.state == "linger" and now >= run.time:
                del self._runs[key]

    @property
    def deadline(self) -> float | None:
        return min((r.time for r in self._runs.values() if r.state != "visible"), default=None)

    @property
    def summary(self) -> str | None:
        messages = [(r.message or "").strip() for r in self._runs.values() if r.state == "visible"]
        if not messages:
            return None
        if messages[0] and all(message == messages[0] for message in messages):
            return messages[0]
        return "Running hooks" if len(messages) > 1 else "Running hook"

    def clear(self) -> None:
        self._runs.clear()
