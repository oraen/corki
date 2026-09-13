"""Structured provenance attached to answers that use long-term memory."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from corki.protocol.ids import ThreadId

_TAGS = (
    ("<oai-mem-citation>", "</oai-mem-citation>"),
    ("<corki-memory-citation>", "</corki-memory-citation>"),
)
# Rust str::trim uses Unicode White_Space, unlike Python's extra U+001C..001F.
_WHITESPACE = (
    "\t\n\v\f\r \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006"
    "\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
)


class ThreadMemoryMode(StrEnum):
    """Public source eligibility; internal pollution is not a user-settable mode."""

    ENABLED = "enabled"
    DISABLED = "disabled"


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
    # Raw provider provenance is not necessarily a valid thread identity. Keep
    # it separate from the validated legacy usage field and default old rows.
    rollout_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", tuple(self.entries))
        object.__setattr__(self, "thread_ids", tuple(self.thread_ids))
        object.__setattr__(self, "rollout_ids", tuple(self.rollout_ids or self.thread_ids))


def parse_memory_citation(text: str) -> tuple[str, MemoryCitation | None]:
    """Hide literal blocks regardless of whether their metadata can be parsed."""

    parser = MemoryCitationStreamFilter(collect_citations=True)
    visible = parser.push(text) + parser.finish()
    entries: list[MemoryCitationEntry] = []
    ids: dict[str, None] = {}
    for body in parser.citations:
        entries_body = _block(body, "<citation_entries>", "</citation_entries>")
        if entries_body is not None:
            entries.extend(
                entry
                for line in entries_body.split("\n")
                if (entry := _parse_entry(line)) is not None
            )
        ids_body = _block(body, "<rollout_ids>", "</rollout_ids>")
        if ids_body is None:
            ids_body = _block(body, "<thread_ids>", "</thread_ids>")
        if ids_body is not None:
            ids.update(
                (value, None) for line in ids_body.split("\n") if (value := line.strip(_WHITESPACE))
            )
    if not entries and not ids:
        return visible, None
    threads = tuple(thread for value in ids if (thread := _thread_id(value)) is not None)
    return visible, MemoryCitation(tuple(entries), threads, tuple(ids))


class MemoryCitationStreamFilter:
    """Literal non-nested tags; malformed/open bodies stay hidden at EOF.

    The UI only retains a possible delimiter prefix. Complete parsing optionally
    collects hidden bodies, using exactly the same visibility state machine.
    """

    def __init__(self, *, collect_citations: bool = False) -> None:
        self._buffer = ""
        self._closing: str | None = None
        self._collect = collect_citations
        self._body: list[str] = []
        self.citations: list[str] = []

    def push(self, text: str) -> str:
        self._buffer += text
        visible: list[str] = []
        while True:
            if self._closing is not None:
                end = self._buffer.find(self._closing)
                if end >= 0:
                    if self._collect:
                        self._body.append(self._buffer[:end])
                        self.citations.append("".join(self._body))
                    self._body.clear()
                    self._buffer = self._buffer[end + len(self._closing) :]
                    self._closing = None
                    continue
                keep = _prefix_suffix(self._buffer, self._closing)
                take = len(self._buffer) - keep
                if self._collect:
                    self._body.append(self._buffer[:take])
                self._buffer = self._buffer[take:]
                break
            matches = [
                (index, opening, closing)
                for opening, closing in _TAGS
                if (index := self._buffer.find(opening)) >= 0
            ]
            if matches:
                index, opening, self._closing = min(matches)
                visible.append(self._buffer[:index])
                self._buffer = self._buffer[index + len(opening) :]
                continue
            keep = max(_prefix_suffix(self._buffer, opening) for opening, _ in _TAGS)
            take = len(self._buffer) - keep
            visible.append(self._buffer[:take])
            self._buffer = self._buffer[take:]
            break
        return "".join(visible)

    def finish(self, *, citation_valid: bool | None = None) -> str:
        """Flush an incomplete opener, not an opened body; legacy keyword ignored."""
        visible = self._buffer if self._closing is None else ""
        if self._closing is not None and self._collect:
            self._body.append(self._buffer)
            self.citations.append("".join(self._body))
        self._buffer = ""
        self._body.clear()
        self._closing = None
        return visible


def _prefix_suffix(text: str, marker: str) -> int:
    for size in range(min(len(text), len(marker) - 1), 0, -1):
        if text.endswith(marker[:size]):
            return size
    return 0


def _block(text: str, opening: str, closing: str) -> str | None:
    start = text.find(opening)
    if start < 0:
        return None
    end = text.find(closing, start + len(opening))
    return text[start + len(opening) : end] if end >= 0 else None


def _parse_entry(line: str) -> MemoryCitationEntry | None:
    try:
        location, note = line.strip(_WHITESPACE).rsplit("|note=[", 1)
        if not note.endswith("]"):
            return None
        path, lines = location.rsplit(":", 1)
        start, end = lines.split("-", 1)
        first, last = _u32(start), _u32(end)
        if first is None or last is None:
            return None
        return MemoryCitationEntry(
            path.strip(_WHITESPACE), first, last, note[:-1].strip(_WHITESPACE)
        )
    except ValueError:
        return None


def _u32(text: str) -> int | None:
    text = text.strip(_WHITESPACE)
    if re.fullmatch(r"\+?[0-9]+", text) is None:
        return None
    # Leading zeroes do not overflow Rust's parser; avoid Python's large-int
    # digit limit without turning a citation into arbitrary-precision work.
    digits = text.removeprefix("+").lstrip("0") or "0"
    if len(digits) > 10:
        return None
    value = int(digits)
    return value if value <= 2**32 - 1 else None


def _thread_id(value: str) -> ThreadId | None:
    # UUID.parse_str accepts simple, hyphenated, braced and URN forms. Restrict
    # shape before Python UUID, which otherwise accepts misplaced hyphens.
    hyphenated = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
    if (
        re.fullmatch(
            rf"(?:[0-9a-fA-F]{{32}}|{hyphenated}|\{{{hyphenated}\}}|urn:uuid:{hyphenated})", value
        )
        is None
    ):
        return None
    return ThreadId(str(UUID(value)))
