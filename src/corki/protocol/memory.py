"""Structured provenance attached to answers that use long-term memory."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from corki.protocol.ids import ThreadId

_OPEN = "<corki-memory-citation>"
_CLOSE = "</corki-memory-citation>"


@dataclass(frozen=True, slots=True)
class MemoryCitationEntry:
    path: str
    line_start: int
    line_end: int
    note: str


@dataclass(frozen=True, slots=True)
class MemoryCitation:
    entries: tuple[MemoryCitationEntry, ...] = ()
    thread_ids: tuple[ThreadId, ...] = ()


def parse_memory_citation(text: str) -> tuple[str, MemoryCitation | None]:
    """Parse a final citation block while leaving malformed prose untouched."""

    start = text.rfind(_OPEN)
    if start < 0:
        return text, None
    end = text.find(_CLOSE, start + len(_OPEN))
    if end < 0 or text[end + len(_CLOSE) :].strip():
        return text, None
    body = text[start + len(_OPEN) : end]
    entries_body = _block(body, "<citation_entries>", "</citation_entries>")
    ids_body = _block(body, "<thread_ids>", "</thread_ids>")
    if entries_body is None or ids_body is None:
        return text, None
    entries = tuple(
        entry
        for line in entries_body.splitlines()
        if (entry := _parse_entry(line.strip())) is not None
    )
    thread_ids = tuple(
        dict.fromkeys(
            ThreadId(value.strip())
            for value in ids_body.splitlines()
            if re.fullmatch(
                r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
                value.strip(),
            )
        )
    )
    if not entries and not thread_ids:
        return text, None
    cleaned = text[:start].rstrip()
    return cleaned, MemoryCitation(entries, thread_ids)


class MemoryCitationStreamFilter:
    """Delay a tiny suffix so internal citation markup never reaches the UI."""

    def __init__(self) -> None:
        self._buffer = ""
        self._capturing = False

    def push(self, text: str) -> str:
        self._buffer += text
        if self._capturing:
            return ""
        marker = self._buffer.find(_OPEN)
        if marker >= 0:
            visible = self._buffer[:marker]
            self._buffer = self._buffer[marker:]
            self._capturing = True
            return visible
        # Hold back only a suffix that could actually grow into the opening
        # marker.  A fixed marker-sized delay would suppress short answers and
        # can deadlock realtime steering when the model pauses after a delta.
        pending = 0
        for size in range(min(len(self._buffer), len(_OPEN) - 1), 0, -1):
            if self._buffer.endswith(_OPEN[:size]):
                pending = size
                break
        visible = self._buffer[:-pending] if pending else self._buffer
        self._buffer = self._buffer[-pending:] if pending else ""
        return visible

    def finish(self, *, citation_valid: bool) -> str:
        visible = "" if citation_valid and self._capturing else self._buffer
        self._buffer = ""
        self._capturing = False
        return visible


def _block(text: str, opening: str, closing: str) -> str | None:
    start = text.find(opening)
    if start < 0:
        return None
    end = text.find(closing, start + len(opening))
    return text[start + len(opening) : end] if end >= 0 else None


def _parse_entry(line: str) -> MemoryCitationEntry | None:
    if not line:
        return None
    match = re.fullmatch(r"(.+):(\d+)-(\d+)\|note=\[(.*)]", line)
    if match is None:
        return None
    path, start, end, note = match.groups()
    logical = PurePosixPath(path.strip())
    line_start, line_end = int(start), int(end)
    if (
        logical.is_absolute()
        or any(part in {"", ".", ".."} for part in logical.parts)
        or line_start < 1
        or line_end < line_start
        or "\n" in note
    ):
        return None
    return MemoryCitationEntry(logical.as_posix(), line_start, line_end, note.strip())
