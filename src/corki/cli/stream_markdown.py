"""Rendered-line ownership for newline-committed assistant source."""

from collections import deque
from time import monotonic

from rich.segment import Segment, Segments

from corki.cli.markdown import AssistantBlock
from corki.cli.markdown_cache import MarkdownParseCache
from corki.cli.markdown_render_cache import MarkdownRenderCache
from corki.cli.stream_animation import ChunkingPolicy


def _render_lines(console, source):
    lines = list(Segment.split_lines(console.render(AssistantBlock(source))))
    # Rich emits placeholder/separator rows for an empty open fence. Committing
    # them would consume the row later occupied by its first real code line.
    # Interior blank rows remain intact; trailing ones resolve with more source
    # or the authoritative final transcript.
    while lines and "".join(segment.text for segment in lines[-1]).strip() in {"", "•"}:
        lines.pop()
    return lines


class StreamMarkdown:
    """Emit new rows; final canonicalization owns structural rewrites.

    Parsing and body rendering retain completed blocks. Emitted screen rows
    must not be mistaken for stable parser state.
    """

    render_lines = staticmethod(_render_lines)

    def __init__(self):
        self.source = ""
        self.width: int | None = None
        self.emitted = 0
        self._queue = deque()
        self.policy = ChunkingPolicy()
        self.parse_cache = MarkdownParseCache()
        self.parse_cache.markdown._block_cache = MarkdownRenderCache()
        self.tail_parse_cache = MarkdownParseCache()
        self.tail_parse_cache.markdown._block_cache = MarkdownRenderCache()
        self.tail_prefix = None

    def tail_lines(self, console, source: str, boundary: int):
        # Use a separate parser: alternating stable-prefix and full-source
        # documents would continually discard the append-only cache state.
        prefix = source[:boundary]
        key = (prefix, console.width)
        if self.tail_prefix is None or self.tail_prefix[0] != key:
            self.tail_prefix = (key, len(self.render_lines(console, prefix)))
        document = self.tail_parse_cache.document(source)
        return self.render_lines(console, document)[self.tail_prefix[1] :]

    def write(self, console, source: str) -> bool:
        self.enqueue(console, source)
        return self.drain(console, self.queued_lines)

    @property
    def queued_lines(self) -> int:
        return len(self._queue)

    def oldest_queued_age(self, now: float) -> float | None:
        return max(0.0, now - self._queue[0][0]) if self._queue else None

    def enqueue(self, console, source: str) -> None:
        resized = self.width is not None and self.width != console.width
        if resized and not self._queue:
            # With no backlog, preserve the existing source-backed reflow rule.
            self.emitted = len(self.render_lines(console, self.parse_cache.document(self.source)))
        lines = self.render_lines(console, self.parse_cache.document(source))
        if resized and self._queue:
            self.emitted = min(self.emitted, len(lines))
            if self.emitted == len(lines) and self.emitted:
                # Wider output must not swallow a previously queued remainder.
                self.emitted -= 1
            self._queue.clear()
        self.width = console.width
        self.source = source
        enqueued = self.emitted + len(self._queue)
        now = monotonic()
        self._queue.extend((now, tuple(line)) for line in lines[enqueued:])

    def drain(self, console, max_lines: int) -> bool:
        remaining = [
            self._queue.popleft()[1] for _ in range(min(max(0, max_lines), len(self._queue)))
        ]
        if not remaining:
            return False
        first = not self.emitted
        # Once handed to output these rows have unknown side effects on failure;
        # never put them back in the queue for automatic replay.
        self.emitted += len(remaining)
        if first:
            console.print()
        console.print(
            Segments([segment for line in remaining for segment in (*line, Segment.line())]),
            end="",
            soft_wrap=True,
        )
        return True
