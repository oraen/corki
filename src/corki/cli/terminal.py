"""Codex-inspired terminal user interface for the CLI adapter.

Rich owns durable output written into terminal scrollback. prompt-toolkit owns
the editable composer. This division is deliberately small and mirrors the
proven approach used by interactive agent CLIs without introducing a full
screen widget framework before Corki needs one.
"""

from __future__ import annotations

import asyncio
from io import StringIO
from pathlib import Path
from time import monotonic

from prompt_toolkit import ANSI, HTML, PromptSession
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import AnyFormattedText
from prompt_toolkit.history import DummyHistory, FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import ConditionalContainer, HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.patch_stdout import patch_stdout
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.segment import Segment, Segments
from rich.text import Text

from corki import __version__
from corki.cli.approval import choose_approval
from corki.cli.approval_details import request_details
from corki.cli.elicitation import collect_elicitation
from corki.cli.history import replay_history
from corki.cli.history_view import HistoryView
from corki.cli.hook_activity import HookActivity
from corki.cli.hook_output import hook_output_text
from corki.cli.input_owner import CycleModeInput, QueuedInput
from corki.cli.markdown import AssistantBlock
from corki.cli.pending_input import pending_input_lines
from corki.cli.plan_stream import PlanStreamUI
from corki.cli.proposed_plan import ProposedPlanBlock
from corki.cli.stream_animation import ChunkingPolicy
from corki.cli.stream_commit import commit_delta
from corki.cli.stream_markdown import StreamMarkdown
from corki.cli.stream_table import TableStreamSource
from corki.cli.terminal_palette import TerminalPalette
from corki.cli.terminal_responses import frame_terminal_responses
from corki.cli.transcript import Transcript, remember_display
from corki.cli.user_input import collect_user_input
from corki.cli.wrapping import wrap_plan_text
from corki.config import CorkiSettings


class TerminalUI(PlanStreamUI):
    """Render Corki output and collect editable user messages."""

    # Rendering the composer is independent of admitting same-turn steering.
    keep_composer_during_turn = True

    def __init__(
        self,
        settings: CorkiSettings,
        history_file: Path,
        *,
        console: Console | None = None,
    ) -> None:
        self._settings = settings
        self._live_input_enabled = settings.realtime_enabled
        self._console = console or Console()
        self._transcript = Transcript(self)
        self._bindings = self._build_key_bindings()
        self._queue_submission = False
        self._queue_editor = None
        self._mode_cycle_enabled = False
        self._mode_cycle_requested = False
        self._reasoning_buffer = ""
        self._reasoning_header = None
        self._reasoning_active = False
        self._reasoning_expanded = False
        self._hook_activity = HookActivity()
        self._hook_timer: asyncio.TimerHandle | None = None

        @self._bindings.add("c-t")
        async def toggle_reasoning_history(event):
            self._history_view.open()

        @self._bindings.add("escape", "up")
        def edit_queued_input(event):
            if self._queue_editor is not None:
                message = self._queue_editor()
                if message is not None:
                    event.current_buffer.document = Document(message, len(message))

        @self._bindings.add("tab")
        def queue_submission(event):
            self._queue_submission = True
            event.current_buffer.validate_and_handle()

        @self._bindings.add(
            "s-tab",
            filter=Condition(self._can_cycle_mode),
        )
        def cycle_mode(event):
            self._draft = event.current_buffer.text
            self._mode_cycle_requested = True
            event.app.exit(result="")

        self._session: PromptSession[str] = PromptSession(
            history=FileHistory(str(history_file)),
            multiline=True,
        )
        self._pending_inputs: tuple[str, ...] = ()
        self._table_source: TableStreamSource | None = None
        self._stream_markdown: StreamMarkdown | None = None
        self._tail_cache = None
        self._session.layout.container = HSplit(
            [
                ConditionalContainer(
                    Window(
                        FormattedTextControl(self._stream_tail_fragments), dont_extend_height=True
                    ),
                    Condition(
                        lambda: (
                            not self._session.app.is_done and bool(self._stream_tail_fragments())
                        )
                    ),
                ),
                ConditionalContainer(
                    Window(
                        FormattedTextControl(self._pending_input_fragments),
                        dont_extend_height=True,
                    ),
                    Condition(lambda: bool(self._pending_inputs) and not self._session.app.is_done),
                ),
                self._session.layout.container,
            ]
        )
        self._history_view = HistoryView(self)
        self._draft = ""
        self._form_session: PromptSession[str] = PromptSession(history=DummyHistory())
        frame_terminal_responses(self._session.app.input)
        frame_terminal_responses(self._form_session.app.input)
        self._terminal_palette = TerminalPalette()
        self._terminal_palette.attach(self._session.app)
        self._terminal_palette.attach(self._form_session.app)
        self._form_bindings = self._build_key_bindings()

        @self._form_bindings.add("c-c")
        def cancel_form(event):
            if event.current_buffer.text:
                event.current_buffer.text = ""
            else:
                event.app.exit(exception=EOFError())

    @staticmethod
    def _build_key_bindings() -> KeyBindings:
        """Create Codex-like composer controls.

        Enter submits the current buffer. Escape followed by Enter inserts a
        newline, which keeps the common single-line flow fast while retaining
        multiline prompts. prompt-toolkit's default Ctrl+C binding raises
        ``KeyboardInterrupt`` and is intentionally left intact.
        """

        bindings = KeyBindings()

        @bindings.add("enter")
        def submit(event) -> None:  # type: ignore[no-untyped-def]
            event.current_buffer.validate_and_handle()

        @bindings.add("escape", "enter")
        def insert_newline(event) -> None:  # type: ignore[no-untyped-def]
            event.current_buffer.insert_text("\n")

        return bindings

    @remember_display
    def show_welcome(self) -> None:
        """Draw the compact session header used at startup and after `/clear`."""

        body = Text()
        body.append(">_ Corki", style="bold")
        body.append(f" (v{__version__})", style="dim")
        body.append("\n\n")
        body.append("model:     ", style="dim")
        body.append(self._settings.model)
        body.append("   /model to change", style="cyan")
        body.append("\n")
        body.append("directory: ", style="dim")
        body.append(str(self._settings.working_directory))

        self._console.print(
            Panel(
                body,
                box=box.ROUNDED,
                border_style="dim",
                padding=(0, 1),
                expand=False,
            )
        )
        self._console.print("\n  [dim]Tip: press Esc then Enter to add a new line[/dim]\n")

    async def read_message(self) -> str | QueuedInput | CycleModeInput:
        """Wait for one message while preserving asynchronous stdout safety."""

        self._queue_submission = False
        self._mode_cycle_requested = False
        with patch_stdout(raw=True):
            try:
                message = await self._session.prompt_async(
                    HTML("<ansicyan><b>›</b></ansicyan> "),
                    placeholder=HTML(
                        '<style fg="ansibrightblack">Ask Corki to do anything</style>'
                    ),
                    bottom_toolbar=self._toolbar,
                    key_bindings=self._bindings,
                    default=self._draft,
                )
            except asyncio.CancelledError:
                self._draft = self._session.default_buffer.text
                raise
            finally:
                self._history_view.close()
            if self._mode_cycle_requested:
                return CycleModeInput()
            self._draft = ""
            if message.strip():
                # PromptSession already echoed it; retain only the replay source here.
                self._transcript.calls.append((TerminalUI._show_submitted_input, (message,), {}))
            return QueuedInput(message) if self._queue_submission else message

    @remember_display
    def _show_submitted_input(self, message: str) -> None:
        self._console.print(Text.assemble(("› ", "cyan bold"), message))

    def replay_history(self, items) -> None:
        replay_history(self, items)

    async def read_elicitation(self, request):
        self._transcript.modal_depth += 1

        async def read(label):
            with patch_stdout(raw=True):
                return await self._form_session.prompt_async(
                    label, key_bindings=self._form_bindings
                )

        def notice(message):
            self._console.print(Text(message))

        async def decide():
            with patch_stdout(raw=True):
                return await choose_approval(
                    self._form_session,
                    details=lambda: request_details(request, light=self._terminal_palette.light),
                    palette=self._terminal_palette,
                    execution=request.kind
                    in {
                        "shell_approval",
                        "patch_approval",
                        "tool_approval",
                        "skill_dependency_install",
                    },
                )

        async def choose_scope(scopes):
            with patch_stdout(raw=True):
                return await choose_approval(
                    self._form_session,
                    execution=True,
                    scopes=scopes,
                    details=lambda: request_details(request, light=self._terminal_palette.light),
                    palette=self._terminal_palette,
                )

        try:
            return await collect_elicitation(
                request, read, notice, decide=decide, choose_scope=choose_scope
            )
        finally:
            self._transcript.modal_depth -= 1
            # Do not retain submitted or cancelled form drafts between requests.
            self._form_session.default_buffer.reset()

    async def read_user_input(self, request):
        self._transcript.modal_depth += 1
        try:
            with patch_stdout(raw=True):
                return await collect_user_input(self._form_session, request)
        finally:
            self._transcript.modal_depth -= 1

    def set_collaboration_mode(self, mode: str) -> None:
        self._collaboration_mode = mode
        self._session.app.invalidate()

    def set_mode_cycle_enabled(self, enabled: bool) -> None:
        self._mode_cycle_enabled = enabled
        self._session.app.invalidate()

    def _can_cycle_mode(self) -> bool:
        return (
            self._mode_cycle_enabled
            and not self._history_view.active
            and not self._transcript.modal_depth
            and self._session.default_buffer.complete_state is None
        )

    def _toolbar(self) -> AnyFormattedText:
        if header := self._hook_activity.summary:
            header = " ".join("".join(c for c in header if c.isprintable() or c.isspace()).split())
            return [("fg:ansibrightblack", f"  {header}   ctrl+t history")]
        if self._can_cycle_mode():
            mode = getattr(self, "_collaboration_mode", self._settings.collaboration_mode)
            label = "  Plan mode" if mode == "plan" else "  Default mode"
            width = self._session.app.output.get_size().columns
            hint = label + " (shift+tab to cycle)"
            text = hint if len(hint) <= width else label
            extra = "   ctrl+t history"
            if len(text + extra) <= width:
                text += extra
            return [("fg:ansicyan" if mode == "plan" else "fg:ansibrightblack", text[:width])]
        if getattr(self, "_collaboration_mode", self._settings.collaboration_mode) == "plan":
            return [("fg:ansicyan", "  Plan mode"), ("", "   ctrl+t history")]
        if self._reasoning_active:
            header = self._reasoning_header or "Thinking"
            # Provider text is a literal, single-line label, never terminal markup.
            header = " ".join("".join(c for c in header if c.isprintable() or c.isspace()).split())
            return [("fg:ansibrightblack", f"  {header}   ctrl+t history")]
        return HTML(
            '  <style fg="ansibrightblack">? for shortcuts   '
            "ctrl+t history   ctrl+c to quit</style>"
        )

    def hook_started(self, run) -> None:
        self._hook_activity.start(run, monotonic())
        self._refresh_hooks()

    def hook_completed(self, run) -> None:
        self._hook_activity.complete(run, monotonic())
        self._refresh_hooks()

    def _refresh_hooks(self) -> None:
        if self._hook_timer is not None:
            self._hook_timer.cancel()
            self._hook_timer = None
        now = monotonic()
        self._hook_activity.advance(now)
        self._session.app.invalidate()
        deadline = self._hook_activity.deadline
        if deadline is not None:
            self._hook_timer = asyncio.get_running_loop().call_later(
                max(0, deadline - now), self._refresh_hooks
            )

    def clear_hooks(self) -> None:
        if not hasattr(self, "_hook_activity"):
            return
        if self._hook_timer is not None:
            self._hook_timer.cancel()
            self._hook_timer = None
        self._hook_activity.clear()
        self._session.app.invalidate()

    def set_history_loader(self, loader) -> None:
        self._history_view.loader = loader

    def set_pending_inputs(self, messages: tuple[str, ...]) -> None:
        self._pending_inputs = tuple(messages)
        self._session.app.invalidate()

    def set_queue_editor(self, editor) -> None:
        self._queue_editor = editor

    def restore_queued_inputs(self, messages: tuple[str, ...]) -> None:
        """Restore after the owned reader has joined, without submitting or writing history."""
        self._draft = "\n".join((*messages, *((self._draft,) if self._draft else ())))

    def _pending_input_fragments(self):
        width = self._session.app.output.get_size().columns
        return [
            ("fg:ansibrightblack italic", line + "\n")
            for line in pending_input_lines(
                self._pending_inputs, width, edit_enabled=self._queue_editor is not None
            )
        ]

    @remember_display
    def show_assistant_message(self, message: str, *, is_error: bool = False) -> None:
        """Render a final runtime response without coupling to runtime types."""

        self._console.print()
        self._console.print(
            Text.assemble(("• ", "red"), message) if is_error else AssistantBlock(message)
        )
        self._console.print()

    @remember_display
    def show_proposed_plan(self, text: str) -> None:
        self._console.print(ProposedPlanBlock(text))

    @remember_display
    def begin_assistant_message(self) -> None:
        """Start one streamed assistant block."""

        self._assistant_pending = ""
        self._assistant_started = False
        self._table_source = (
            TableStreamSource() if self._console.is_terminal and self._live_input_enabled else None
        )
        self._stream_markdown = StreamMarkdown() if self._table_source is not None else None

    def set_live_input_enabled(self, enabled: bool) -> None:
        self._live_input_enabled = enabled

    def _stream_tail_fragments(self):
        if self._stream_markdown is None:
            return self._plan_tail_fragments()
        table = self._table_source
        if table is None or not table.tail:
            return []
        if self._stream_markdown is not None and self._stream_markdown.queued_lines:
            # A mutable tail must not overtake its not-yet-visible stable prefix.
            return []
        source = table.source
        width = self._session.app.output.get_size().columns
        key = (source, table.emitted, width)
        if self._tail_cache is None or self._tail_cache[0] != key:
            output = StringIO()
            console = Console(file=output, width=max(1, width), force_terminal=True)
            # The boundary is a source offset, not an independently parseable
            # document. Preserve fence/list/quote context by slicing rendered rows.
            rows = self._stream_markdown.tail_lines(console, source, table.emitted)
            console.print(
                Segments([part for row in rows for part in (*row, Segment.line())]),
                end="",
                soft_wrap=True,
            )
            self._tail_cache = (key, ANSI(output.getvalue()).__pt_formatted_text__())
        return self._tail_cache[1]

    def _write_assistant_stream(self, text: str) -> None:
        if not self._assistant_started:
            self._console.print()
            self._console.print("• ", style="green", end="")
            self._assistant_started = True
        self._console.print(text, end="", markup=False, highlight=False, soft_wrap=True)

    async def append_assistant_delta_live(self, delta: str) -> None:
        if getattr(self, "_animation_enabled", False) and self._stream_markdown is not None:
            # Enqueueing does not write to the terminal. Only actual drains
            # suspend/redraw the composer.
            self.append_assistant_delta(delta)
            await self.commit_stream_tick(catch_up_only=True)
        else:
            await commit_delta(self, delta)

    def enable_stream_animation(self) -> bool:
        self._animation_clock_owner = object()
        self._animation_policy = ChunkingPolicy()
        self._animation_enabled = self._console.is_terminal and self._live_input_enabled
        return self._animation_enabled

    def stream_animation_owner(self):
        streams = (self._stream_markdown, getattr(self, "_plan_stream", None))
        return (
            self._animation_clock_owner
            if any(s is not None and s.queued_lines for s in streams)
            else None
        )

    async def commit_stream_tick(self, *, catch_up_only=False, finish=False):
        streams = [
            s for s in (self._stream_markdown, getattr(self, "_plan_stream", None)) if s is not None
        ]
        if not streams:
            return
        for stream in streams:
            stream.enqueue(self._console, stream.source)
        now = monotonic()
        depth = sum(s.queued_lines for s in streams)
        age = max((s.oldest_queued_age(now) for s in streams if s.queued_lines), default=None)
        count = (
            depth
            if finish
            else self._animation_policy.decide(
                depth,
                age,
                now,
                catch_up_only=catch_up_only,
            )
        )
        if not count:
            return

        def drain():
            for stream in streams:
                wrote = stream.drain(self._console, count)
                if stream is self._stream_markdown:
                    self._assistant_started |= wrote
            if not any(s.queued_lines for s in streams):
                self._animation_policy.decide(0, None, now)

        await commit_delta(self, "\n", operation=drain)

    async def finish_stream_animation(self):
        try:
            await self.commit_stream_tick(finish=True)
        finally:
            self._animation_enabled = False

    @remember_display
    def append_assistant_delta(self, delta: str) -> None:
        """Only commit complete source lines; an unfinished line remains provisional."""

        pending = self._assistant_pending + delta
        boundary = pending.rfind("\n") + 1
        self._assistant_pending = pending[boundary:]
        if boundary:
            stable = pending[:boundary]
            if self._table_source is not None:
                stable = self._table_source.push(stable)
                if not self._transcript.replaying:
                    self._session.app.invalidate()
            if stable:
                if self._stream_markdown is not None:
                    source = self._table_source.source[: self._table_source.emitted]
                    if (
                        getattr(self, "_animation_enabled", False)
                        and not self._transcript.replaying
                    ):
                        self._stream_markdown.enqueue(self._console, source)
                    else:
                        self._assistant_started |= self._stream_markdown.write(
                            self._console, source
                        )
                else:
                    self._write_assistant_stream(stable)

    @remember_display
    def end_assistant_message(self) -> None:
        """Terminate the active streamed assistant block."""

        self._assistant_pending = ""
        had_table_source = self._table_source is not None
        self._table_source = None
        self._stream_markdown = None
        self._tail_cache = None
        if had_table_source and not self._transcript.replaying:
            self._session.app.invalidate()
        if self._assistant_started:
            self._console.print("\n")
        self._assistant_started = False

    async def complete_assistant_message(self, text: str) -> None:
        if getattr(self, "_animation_enabled", False):
            await self.commit_stream_tick(finish=True)
        pending = self._assistant_pending
        if pending and (
            not self._console.is_terminal or (not self._assistant_started and text == pending)
        ):
            # Pipes keep their append-only representation. A short, plain TTY
            # answer with no committed lines can finish without a full repair.
            with self._history_view.capture_output():
                self._write_assistant_stream(pending)
        self.end_assistant_message()
        if self._transcript.complete_assistant(text):
            if self._console.is_terminal:
                await self._transcript.repair()
            else:
                # A pipe cannot erase previously streamed bytes; emit the authoritative result.
                TerminalUI.show_assistant_message.__wrapped__(self, text)

    @remember_display
    def begin_reasoning(self) -> None:
        """Start a visually separate provider reasoning stream."""

        if not self._transcript.include_reasoning:
            self._reasoning_buffer = ""
            self._reasoning_header = None
            self._reasoning_active = True
            if not self._transcript.replaying:
                self._session.app.invalidate()
            return
        self._console.print()
        self._console.print("◦ thinking  ", style="dim italic", end="")

    @remember_display
    def append_reasoning_delta(self, delta: str) -> None:
        """Render reasoning softly without mixing it into assistant content."""

        if not self._transcript.include_reasoning:
            self._reasoning_buffer += delta
            if self._reasoning_header is None:
                start = self._reasoning_buffer.find("**")
                end = self._reasoning_buffer.find("**", start + 2) if start >= 0 else -1
                if end >= 0:
                    self._reasoning_header = self._reasoning_buffer[start + 2 : end].strip() or None
            if not self._transcript.replaying:
                self._session.app.invalidate()
            return
        self._console.print(
            delta,
            style="dim italic",
            end="",
            markup=False,
            highlight=False,
            soft_wrap=True,
        )

    @remember_display
    def end_reasoning(self) -> None:
        """Terminate the current reasoning stream."""

        if not self._transcript.include_reasoning:
            self._reasoning_buffer = ""
            self._reasoning_header = None
            self._reasoning_active = False
            if not self._transcript.replaying:
                self._session.app.invalidate()
            return
        self._console.print("\n")

    @remember_display
    def show_tool_started(self, name: str, arguments_preview: str) -> None:
        """Render a compact tool-call header; detailed output follows separately."""

        preview = arguments_preview.replace("\n", " ")
        if len(preview) > 180:
            preview = preview[:177] + "..."
        self._console.print(Text.assemble(("• ", "cyan"), (name, "bold"), (f" {preview}", "dim")))

    @remember_display
    def show_tool_output(self, output: str) -> None:
        """Display bounded evidence returned by a tool."""

        if output:
            self._console.print(output, style="dim", markup=False, highlight=False)

    @remember_display
    def show_tool_completed(self, name: str, *, is_error: bool) -> None:
        """Make tool failure visible without duplicating successful output."""

        if is_error:
            self._console.print(Text(f"  {name} failed", style="red"))

    @remember_display
    def show_plan(
        self, plan: tuple[dict[str, str], ...], *, explanation: str | None = None
    ) -> None:
        """Render the latest user-visible plan."""

        self._console.print(Text.assemble(("• ", "dim"), ("Updated Plan", "bold")))
        styles = {
            "completed": ("✔ ", "dim strike"),
            "in_progress": ("□ ", "cyan bold"),
            "pending": ("□ ", "dim"),
        }
        first = True
        if explanation and explanation.strip():
            for line in wrap_plan_text(
                Text(explanation.strip(), style="dim italic"),
                self._console,
                self._console.width - 4,
            ):
                self._console.print(
                    Text.assemble(("  └ " if first else "    ", "dim"), line), soft_wrap=True
                )
                first = False
        if not plan:
            prefix = "  └ " if first else "    "
            self._console.print(Text(prefix + "(no steps provided)", style="dim italic"))
        for item in plan:
            marker, style = styles.get(item["status"], styles["pending"])
            text = Text(item["step"], style=style)
            for index, line in enumerate(
                wrap_plan_text(text, self._console, self._console.width - 6)
            ):
                self._console.print(
                    Text.assemble(
                        ("  └ " if first else "    ", "dim"),
                        marker if index == 0 else "  ",
                        line,
                    ),
                    soft_wrap=True,
                )
                first = False

    @remember_display
    def show_hook_output(self, run) -> None:
        """Keep completion status and literal output styled during transcript reflow."""
        text = hook_output_text(run)
        if text:
            self._console.print()
            self._console.print(text)
            self._console.print()

    @remember_display
    def show_notice(self, message: str) -> None:
        """Render local command output in the transcript."""

        self._console.print()
        self._console.print(Text(message), style="dim")
        self._console.print()

    def clear(self) -> None:
        """Clear terminal scrollback and restore the startup header."""

        self.clear_hooks()
        view = getattr(self, "_history_view", None)
        if view is not None and view.loader is not None:
            view.loader.stop()
            view.loader = None
        self._console.clear()
        self._transcript.calls.clear()
        self._transcript.repair_pending = False
        self._transcript.stream_reflowed = False
        self.show_welcome()

    async def watch_resize(self) -> None:
        await self._transcript.watch()

    def show_goodbye(self) -> None:
        """Leave a concise, deterministic shutdown message."""

        self.clear_hooks()
        self._console.print("\n[dim]Session ended.[/dim]")
