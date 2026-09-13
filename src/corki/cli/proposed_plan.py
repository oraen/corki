"""Source-backed final plan cell, separate from ordinary assistant gutters."""

from rich.segment import Segment, Segments
from rich.text import Text

from corki.cli.markdown import AssistantMarkdown
from corki.cli.stream_markdown import StreamMarkdown


class ProposedPlanBlock:
    def __init__(self, source: str):
        self.source = source

    def __rich_console__(self, console, options):
        yield Text.assemble(("• ", "dim"), ("Proposed Plan", "bold"))
        yield Text(" ")
        yield Text(" ")
        width = max(1, options.max_width - 4)
        lines = list(
            Segment.split_lines(
                console.render(
                    self.source
                    if isinstance(self.source, AssistantMarkdown)
                    else AssistantMarkdown(self.source),
                    options.update(width=width),
                )
            )
        )
        while lines and not any(segment.text.strip() for segment in lines[-1]):
            lines.pop()
        if not lines:
            yield Text("  (empty)", style="dim italic")
        for line in lines:
            text = Text()
            for segment in line:
                text.append(segment.text, segment.style)
            for wrapped in text.wrap(console, width):
                yield Text("  ") + wrapped
        yield Text(" ")


class StreamPlan(StreamMarkdown):
    """Queue body rows only; header/padding belong to the first committed batch."""

    @staticmethod
    def render_lines(console, source):
        raw = source.markup if isinstance(source, AssistantMarkdown) else source
        if not raw:
            return []
        lines = list(Segment.split_lines(console.render(ProposedPlanBlock(source))))[3:-1]
        while lines and not any(segment.text.strip() for segment in lines[-1]):
            lines.pop()
        return lines

    def drain(self, console, max_lines):
        rows = [self._queue.popleft()[1] for _ in range(min(max(0, max_lines), self.queued_lines))]
        if not rows:
            return False
        first = not self.emitted
        self.emitted += len(rows)
        self.committed_rows.extend(rows)
        if first:
            console.print(Text.assemble(("• ", "dim"), ("Proposed Plan", "bold")))
            console.print(" \n ")
        console.print(
            Segments([s for row in rows for s in (*row, Segment.line())]), end="", soft_wrap=True
        )
        return True

    def __init__(self):
        super().__init__()
        self.committed_rows = []
