"""Immutable host view of a live, Runtime-owned background terminal."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class BackgroundTerminalInfo:
    item_id: str
    process_id: str
    command: str
    cwd: Path
