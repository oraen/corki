"""Bounded local draft recall; persisted input history remains text-only like Codex."""

from dataclasses import dataclass

from prompt_toolkit.history import FileHistory

MAX_HISTORY_ENTRIES = 128
# Leave room for a maximum legal submission plus its rich placeholder metadata.
MAX_HISTORY_TEXT_CHARS = 4 * (1 << 20)
MAX_HISTORY_READ_BYTES = 16 * (1 << 20)


class ExpandedFileHistory(FileHistory):
    """Disk history stores expanded text, not unusable private paste labels."""

    def __init__(self, filename, expand):
        super().__init__(filename)
        self.expand = expand

    def append_string(self, string):
        super().append_string(self.expand(string))
        size = sum(map(len, self._loaded_strings))
        while self._loaded_strings and (
            len(self._loaded_strings) > MAX_HISTORY_ENTRIES or size > MAX_HISTORY_TEXT_CHARS
        ):
            size -= len(self._loaded_strings.pop())

    def load_history_strings(self):
        try:
            with open(self.filename, "rb") as source:
                source.seek(0, 2)
                start = max(0, source.tell() - MAX_HISTORY_READ_BYTES)
                source.seek(start)
                raw = source.read(MAX_HISTORY_READ_BYTES)
        except FileNotFoundError:
            return iter(())
        if start:
            # Never manufacture a prompt from the tail of a truncated entry.
            boundary = raw.find(b"\n# ")
            raw = raw[boundary + 1 :] if boundary >= 0 else b""
        entries, lines, size = [], [], 0
        for line in raw.decode("utf-8", errors="replace").split("\n"):
            if line.startswith("+"):
                lines.append(line[1:])
            elif lines:
                entry = "\n".join(lines)
                lines.clear()
                entries.append(entry)
                size += len(entry)
                while entries and (
                    len(entries) > MAX_HISTORY_ENTRIES or size > MAX_HISTORY_TEXT_CHARS
                ):
                    size -= len(entries.pop(0))
        # Files written by FileHistory end in a newline. An unfinished final
        # record is deliberately not recovered as if it were fully persisted.
        return reversed(entries)


@dataclass(frozen=True)
class DraftEntry:
    text: str
    images: tuple = ()
    positions: tuple = ()
    pastes: tuple = ()
    bindings: tuple = ()

    @classmethod
    def capture(cls, draft):
        return cls(draft.text, draft.images, draft.positions, draft.pastes, draft.bindings)


class DraftHistory:
    def __init__(self):
        self.entries = []
        self.index = 0
        self.scratch = None
        self.loaded = False

    def record(self, entry):
        if entry.text and (not self.entries or self.entries[-1] != entry):
            self.entries.append(entry)
        # Drop whole old entries, never leave an orphan image label in a local entry.
        while self.entries:
            images = {id(image): image for e in self.entries for image in e.images}
            if (
                len(self.entries) <= MAX_HISTORY_ENTRIES
                and sum(
                    len(e.text)
                    + sum(len(p[2]) for p in e.pastes)
                    + sum(len(b[1]) + len(b[2].path) for b in e.bindings)
                    for e in self.entries
                )
                <= MAX_HISTORY_TEXT_CHARS
                and sum(len(i.data_url) for i in images.values()) <= 44_000_000
            ):
                break
            self.entries.pop(0)
        self.reset_navigation()

    def reset_navigation(self):
        self.index = len(self.entries)
        self.scratch = None

    def move(self, direction, current):
        target = max(0, min(len(self.entries), self.index + direction))
        if target == self.index:
            return None
        if self.index == len(self.entries):
            self.scratch = current
        self.index = target
        return self.scratch if target == len(self.entries) else self.entries[target]

    def should_navigate(self, text, cursor):
        if not self.entries:
            return False
        if not text:
            return True
        return (
            cursor in (0, len(text))
            and self.index < len(self.entries)
            and self.entries[self.index].text == text
        )

    def clear(self):
        self.entries.clear()
        self.reset_navigation()
