"""Plan stream ownership on TerminalUI, independent of assistant message state."""

from io import StringIO

from prompt_toolkit import ANSI
from rich.console import Console
from rich.segment import Segment, Segments
from rich.text import Text

from corki.cli.proposed_plan import StreamPlan
from corki.cli.stream_commit import commit_delta
from corki.cli.stream_table import TableStreamSource
from corki.cli.transcript import remember_display


class PlanStreamUI:
    def _plan_tail_fragments(self):
        if not self._console.is_terminal or not self._live_input_enabled:
            return []
        stream = getattr(self, "_plan_stream", None)
        table = getattr(self, "_plan_table", None)
        if stream is None or table is None or not table.tail or stream.queued_lines:
            return []
        width = self._session.app.output.get_size().columns
        key = (id(stream), table.source, table.emitted, stream.emitted, width)
        cached = getattr(self, "_plan_tail_cache", None)
        if cached is None or cached[0] != key:
            output = StringIO()
            console = Console(file=output, width=max(1, width), force_terminal=True)
            rows = stream.tail_lines(console, table.source, table.emitted)
            if rows:
                if not stream.emitted:
                    console.print(Text.assemble(("• ", "dim"), ("Proposed Plan", "bold")))
                    console.print(" \n ")
                console.print(
                    Segments([s for row in rows for s in (*row, Segment.line())]),
                    end="",
                    soft_wrap=True,
                )
            self._plan_tail_cache = (key, ANSI(output.getvalue()).__pt_formatted_text__())
        return self._plan_tail_cache[1]

    @remember_display
    def begin_proposed_plan(self, item_id=None):
        self._plan_tail_cache = None
        self._plan_stream = StreamPlan()
        self._plan_table = TableStreamSource()
        self._plan_pending = ""

    @remember_display
    def append_proposed_plan_delta(self, delta):
        pending = self._plan_pending + delta
        boundary = pending.rfind("\n") + 1
        self._plan_pending = pending[boundary:]
        if boundary:
            self._plan_table.push(pending[:boundary])
            if not self._transcript.replaying:
                self._session.app.invalidate()
            if not self._console.is_terminal:
                return
            source = self._plan_table.source[: self._plan_table.emitted]
            self._plan_stream.enqueue(self._console, source)
            if not getattr(self, "_animation_enabled", False) or self._transcript.replaying:
                self._plan_stream.drain(self._console, self._plan_stream.queued_lines)

    async def append_proposed_plan_delta_live(self, delta):
        if getattr(self, "_animation_enabled", False):
            self.append_proposed_plan_delta(delta)
            await self.commit_stream_tick(catch_up_only=True)
        else:
            await commit_delta(
                self, delta, operation=lambda: self.append_proposed_plan_delta(delta)
            )

    @remember_display
    def end_proposed_plan(self, *, completed=False):
        # Uncommitted rows/tails are provisional, never promoted on interruption.
        stream = getattr(self, "_plan_stream", None)
        if stream is not None and not completed and not self._transcript.replaying:
            self._transcript.interrupt_plan(tuple(stream.committed_rows))
        self._plan_stream = self._plan_table = None
        self._plan_tail_cache = None
        self._plan_pending = ""
        if not self._transcript.replaying:
            self._session.app.invalidate()

    @remember_display
    def show_proposed_plan_fragment(self, rows):
        if rows:
            self._console.print(Text.assemble(("• ", "dim"), ("Proposed Plan", "bold")))
            self._console.print(" \n ")
            self._console.print(
                Segments([s for row in rows for s in (*row, Segment.line())]),
                end="",
                soft_wrap=True,
            )

    async def complete_proposed_plan(self, text):
        stream = getattr(self, "_plan_stream", None)
        if stream is None:
            self.show_proposed_plan(text)
            return
        if not self._console.is_terminal:
            # A pipe cannot replace already emitted draft blocks. Keep its plan
            # output completion-only while interactive terminals stream drafts.
            self.end_proposed_plan(completed=True)
            self._transcript.complete_plan(text)
            type(self).show_proposed_plan.__wrapped__(self, text)
            return

        def finish():
            stream.enqueue(self._console, text)
            stream.drain(self._console, stream.queued_lines)
            if stream.emitted:
                self._console.print(" ")

        await commit_delta(self, "\n", operation=finish)
        self.end_proposed_plan(completed=True)
        if self._transcript.complete_plan(text):
            await self._transcript.repair()
