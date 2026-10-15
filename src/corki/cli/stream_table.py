"""Append-only source partition for mutable pipe tables, independent of rendering."""

import re


def _unquote(line: str) -> str:
    line = line.lstrip()
    while line.startswith(">"):
        line = line[1:].lstrip()
    return line


def _segments(line: str, *, strip_quotes: bool = True) -> list[str] | None:
    line = (_unquote(line) if strip_quotes else line).strip()
    if not line:
        return None
    outer = line.startswith("|") or line.endswith("|")
    content = line.removeprefix("|").removesuffix("|")
    parts, start, index = [], 0, 0
    while index < len(content):
        if content[index] == "\\":
            index += 2
            continue
        if content[index] == "|":
            parts.append(content[start:index].strip())
            start = index + 1
        index += 1
    parts.append(content[start:].strip())
    return parts if outer or len(parts) > 1 else None


class TableStreamSource:
    """Scan complete source lines; character offsets stay local to Python strings.

    Candidate headers remain mutable across blank lines. Only adjacent header and
    delimiter lines confirm a table; once confirmed, hold through finalization.
    """

    def __init__(self):
        self.source = ""
        self.emitted = 0
        self.pending: int | None = None
        self.confirmed: int | None = None
        self.previous: tuple[int, bool] | None = None
        self.fence: tuple[str, int, bool] | None = None

    @property
    def tail(self) -> str:
        boundary = self.confirmed if self.confirmed is not None else self.pending
        return self.source[boundary:] if boundary is not None else ""

    def push(self, chunk: str) -> str:
        if chunk and not chunk.endswith("\n"):
            raise ValueError("table scanner requires complete source lines")
        for raw_line in chunk.split("\n")[:-1]:
            line = raw_line + "\n"
            start = len(self.source)
            self.source += line
            allowed = self.fence is None or self.fence[2]
            segments = _segments(line) if allowed else None
            header = segments is not None and any(segments)
            delimiter = segments is not None and all(
                re.fullmatch(r":?-{3,}:?", part) for part in segments
            )
            if self.confirmed is None:
                if self.previous is not None and self.previous[1] and delimiter:
                    self.confirmed = self.previous[0]
                    self.pending = None
                elif line.strip():
                    self.pending = start if header else None
            self.previous = (start, header)
            if len(line) - len(line.lstrip(" ")) <= 3:
                candidate = _unquote(line)
                match = re.match(r"(`{3,}|~{3,})(.*)", candidate)
                if match:
                    marker, info = match.groups()
                    if self.fence is None:
                        language = info.split()[0].lower() if info.split() else ""
                        self.fence = (marker[0], len(marker), language in {"md", "markdown"})
                    elif (
                        marker[0] == self.fence[0]
                        and len(marker) >= self.fence[1]
                        and not info.strip()
                    ):
                        self.fence = None
        boundary = self.confirmed if self.confirmed is not None else self.pending
        boundary = len(self.source) if boundary is None else boundary
        stable = self.source[self.emitted : boundary]
        self.emitted = boundary
        return stable
