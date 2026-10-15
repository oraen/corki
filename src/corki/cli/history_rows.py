"""Owned disk-backed styled rows with a bounded visible-row cache."""

import json
import struct
import tempfile
import weakref
from collections import OrderedDict
from contextlib import ExitStack

from prompt_toolkit import ANSI


class HistoryRows:
    """Streaming Rich/ANSI sink; hidden rows never become a full fragment list.

    Canonical history is owned elsewhere. Both temporary files are private,
    automatically removed on close, and capped together. Capacity failures are
    explicit; callers must keep canonical history and close a failed build.
    """

    encoding = "utf-8"

    def __init__(self, *, max_bytes=512 * 1024 * 1024, cache_bytes=1024 * 1024):
        self._owner = ExitStack()
        try:
            self._data = self._owner.enter_context(tempfile.TemporaryFile())  # noqa: SIM115
            self._index = self._owner.enter_context(tempfile.TemporaryFile())  # noqa: SIM115
        except BaseException:
            self._owner.close()
            raise
        self._finalizer = weakref.finalize(self, self._owner.close)
        self.max_bytes, self.cache_bytes = max_bytes, cache_bytes
        self._count = self._size = self._cached_bytes = 0
        self._cache = OrderedDict()
        self._line = []
        self._chars = 0
        self._sealed = self.closed = False
        self._ansi = ANSI("")
        # Use the same style parser as the existing UI, but redirect its append
        # sink. The parser captures this sink when primed, preserving SGR state
        # across Rich writes and line boundaries without storing hidden chars.
        self._ansi._formatted_text = self
        self._parser = self._ansi._parse_corot()
        next(self._parser)

    def write(self, text):
        if self.closed or self._sealed:
            raise ValueError("history rows are not writable")
        for char in text:
            self._parser.send(char)
        return len(text)

    def flush(self):
        pass

    def append(self, fragment):
        if self.closed or self._sealed:
            raise ValueError("history rows are not writable")
        style, text = fragment[:2]
        for index, part in enumerate(text.split("\n")):
            if index:
                self._commit()
            if not part:
                continue
            self._chars += len(part)
            if self._chars > 65536:
                raise ValueError("history row exceeds its display limit")
            if self._line and self._line[-1][0] == style:
                self._line[-1][1].append(part)
            else:
                self._line.append((style, [part]))

    def _commit(self):
        row = [(style, "".join(parts)) for style, parts in self._line]
        data = json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if self._size + len(data) + 16 > self.max_bytes:
            raise ValueError("history display cache exceeds its limit")
        self._index.write(struct.pack("<QQ", self._data.tell(), len(data)))
        self._data.write(data)
        self._size += len(data) + 16
        self._count += 1
        self._line.clear()
        self._chars = 0

    def finish(self):
        if self.closed:
            raise ValueError("history rows are closed")
        if not self._sealed:
            self._commit()  # split_lines also retains an empty trailing line.
            self._sealed = True
            self._parser.close()
            self._ansi._formatted_text = []  # break the parser sink's self-reference
        return self

    def __len__(self):
        return self._count

    def __bool__(self):
        # File-like sinks must be truthy even before their first row; Rich uses
        # `file or sys.stdout` when selecting its destination.
        return True

    def __getitem__(self, index):
        if self.closed:
            raise ValueError("history rows are closed")
        if not self._sealed:
            raise ValueError("history rows are not finished")
        if index < 0:
            index += self._count
        if not 0 <= index < self._count:
            raise IndexError(index)
        if index in self._cache:
            row, _ = self._cache[index]
            self._cache.move_to_end(index)
            return row
        self._index.seek(index * 16)
        offset, size = struct.unpack("<QQ", self._index.read(16))
        self._data.seek(offset)
        row = [tuple(part) for part in json.loads(self._data.read(size))]
        if size <= self.cache_bytes:
            self._cache[index] = row, size
            self._cached_bytes += size
            while len(self._cache) > 128 or self._cached_bytes > self.cache_bytes:
                _, (_, removed) = self._cache.popitem(last=False)
                self._cached_bytes -= removed
        return row

    def close(self):
        if self.closed:
            return
        self.closed = True
        self._parser.close()
        self._line.clear()
        self._cache.clear()
        self._cached_bytes = 0
        self._ansi._formatted_text = []
        self._finalizer()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
