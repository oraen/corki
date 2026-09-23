"""Codex-inspired terminal user interface for the CLI adapter.

Rich owns durable output written into terminal scrollback. prompt-toolkit owns
the editable composer. This division is deliberately small and mirrors the
proven approach used by interactive agent CLIs without introducing a full
screen widget framework before Corki needs one.
"""

from __future__ import annotations

import asyncio
from contextlib import ExitStack, contextmanager
from io import StringIO
from pathlib import Path
from time import monotonic

from prompt_toolkit import ANSI, HTML, PromptSession
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import AnyFormattedText, to_formatted_text
from prompt_toolkit.history import DummyHistory, FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import ConditionalContainer, HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.patch_stdout import patch_stdout
from rich import box
from rich.cells import cell_len, set_cell_size
from rich.console import Console
from rich.panel import Panel
from rich.segment import Segment, Segments
from rich.text import Text

from corki import __version__
from corki.cli.approval import choose_approval
from corki.cli.approval_details import request_details
from corki.cli.command_completion import (
    CommandCompleter,
    bind_command_navigation,
    complete_command,
)
from corki.cli.display_text import visible_terminal_text
from corki.cli.elicitation import collect_elicitation
from corki.cli.history import replay_history
from corki.cli.history_view import HistoryView
from corki.cli.hook_activity import HookActivity
from corki.cli.hook_output import hook_output_text
from corki.cli.input_owner import CycleModeInput, QueuedInput
from corki.cli.keyboard import enhanced_keyboard
from corki.cli.markdown import AssistantBlock
from corki.cli.pending_input import pending_input_lines
from corki.cli.plan_stream import PlanStreamUI
from corki.cli.proposed_plan import ProposedPlanBlock
from corki.cli.stream_animation import ChunkingPolicy
from corki.cli.stream_commit import commit_delta
from corki.cli.stream_markdown import StreamMarkdown
from corki.cli.stream_table import TableStreamSource
from corki.cli.terminal_console import TerminalConsole
from corki.cli.terminal_palette import TerminalPalette
from corki.cli.terminal_responses import frame_terminal_responses
from corki.cli.transcript import Transcript, remember_display
from corki.cli.user_input import collect_user_input
from corki.cli.working_status import WorkingStatus
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
        self._console = console or TerminalConsole()
        self._working_status = WorkingStatus(
            lambda: self._session.app.invalidate(), animated=self._console.is_terminal
        )
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
        self._turn_active = False
        self._active_tools: dict[str, str] = {}
        self._hook_activity = HookActivity()
        self._hook_timer: asyncio.TimerHandle | None = None
        self._mcp_loading = False
        self._model_menu = None

        @self._bindings.add(
            "c-c",
            filter=Condition(
                lambda: not self._history_view.active and bool(self._session.default_buffer.text)
            ),
        )
        def clear_composer_draft(event):
            # Codex consumes Ctrl+C on nonempty composer input before interrupt/quit.
            # Keep the cleared draft recoverable through input history, not model history.
            event.current_buffer.append_to_history()
            event.current_buffer.reset()
            self._draft = ""

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
            if complete_command(event.current_buffer, trailing_space=True):
                return
            self._queue_submission = True
            event.current_buffer.validate_and_handle()

        @self._bindings.add("enter")
        def submit_composer(event):
            complete_command(event.current_buffer)
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
            completer=CommandCompleter(),
            complete_while_typing=True,
        )
        # Once the terminal has decoded Escape, don't hold the popup open for
        # the default one-second multi-key binding timeout. Alt+Enter delivered
        # as a chord still uses the existing newline binding.
        self._session.app.timeoutlen = 0.1
        self._last_composer_activity: float | None = None
        composer_before_key = ""

        def before_composer_key(processor):
            nonlocal composer_before_key
            composer_before_key = self._session.default_buffer.text

        def after_composer_key(processor):
            # Observe actual editing/submission, not buffer resets when restoring
            # a draft or merely moving the cursor. CPR replies are excluded by
            # prompt-toolkit's key processor from these callbacks.
            if not self._history_view.active and (
                self._session.default_buffer.text != composer_before_key
                or self._session.app.is_done
            ):
                self._last_composer_activity = monotonic()

        self._session.app.key_processor.before_key_press += before_composer_key
        self._session.app.key_processor.after_key_press += after_composer_key

        @self._bindings.add(
            "escape",
            filter=Condition(
                lambda: (
                    not self._history_view.active
                    and self._session.default_buffer.complete_state is not None
                )
            ),
        )
        def dismiss_command_completion(event):
            buffer = event.current_buffer
            buffer.cancel_completion()
            self._session.completer.dismissed_text = buffer.text

        def clear_completion_dismissal(buffer):
            completer = self._session.completer
            if buffer.text != completer.dismissed_text:
                completer.dismissed_text = None

        self._session.default_buffer.on_text_changed += clear_completion_dismissal
        self._pending_inputs: tuple[str, ...] = ()
        self._table_source: TableStreamSource | None = None
        self._stream_markdown: StreamMarkdown | None = None
        self._tail_cache = None
        self._session.layout.container = HSplit(
            [
                ConditionalContainer(
                    Window(
                        FormattedTextControl([("fg:ansicyan", "  Loading MCP tools…")]),
                        height=1,
                    ),
                    Condition(lambda: self._mcp_loading and not self._history_view.active),
                ),
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
                ConditionalContainer(
                    Window(FormattedTextControl(self._working_fragments), dont_extend_height=True),
                    Condition(
                        lambda: (
                            self._turn_active
                            and not self._session.app.is_done
                            and not self._history_view.active
                        )
                    ),
                ),
                self._session.layout.container,
                ConditionalContainer(
                    Window(FormattedTextControl(self._toolbar), height=1),
                    Condition(lambda: not self._session.app.is_done and not self._turn_active),
                ),
            ]
        )
        self._history_view = HistoryView(self)
        bind_command_navigation(self._bindings, self._session, self._history_view)
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

        Enter submits the current buffer. Enhanced modified Enter is decoded
        as Ctrl+J; it and Escape followed by Enter insert a newline.
        prompt-toolkit's default Ctrl+C binding raises
        ``KeyboardInterrupt`` and is intentionally left intact.
        """

        bindings = KeyBindings()

        @bindings.add("enter")
        def submit(event) -> None:  # type: ignore[no-untyped-def]
            event.current_buffer.validate_and_handle()

        @bindings.add("escape", "enter")
        @bindings.add("c-j")
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
        body.append(visible_terminal_text(self._settings.model))
        body.append("   /model to change", style="cyan")
        body.append("\n")
        body.append("directory: ", style="dim")
        body.append(visible_terminal_text(str(self._settings.working_directory)))

        self._console.print(
            Panel(
                body,
                box=box.ROUNDED,
                border_style="dim",
                padding=(0, 1),
                expand=False,
            )
        )
        self._console.print(
            "\n  [dim]Tip: Shift+Enter adds a new line; "
            "Alt+Enter or Esc then Enter also works[/dim]\n"
        )

    async def read_message(self) -> str | QueuedInput | CycleModeInput:
        """Wait for one message while preserving asynchronous stdout safety."""

        scope = getattr(self, "_input_scope", None)
        if scope is not None and not self._input_raw:
            scope.enter_context(self._session.app.input.raw_mode())
            self._input_raw = True
        self._queue_submission = False
        self._mode_cycle_requested = False
        self._session.completer.dismissed_text = None
        with patch_stdout(raw=True), enhanced_keyboard(self._session.app):
            try:
                message = await self._session.prompt_async(
                    HTML("<ansicyan><b>›</b></ansicyan> "),
                    placeholder=HTML(
                        '<style fg="ansibrightblack">Ask Corki to do anything</style>'
                    ),
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

    @contextmanager
    def input_mode(self):
        # Keep CR distinct from Ctrl+J while switching between modal/composer
        # readers. Restoring cooked mode in those gaps converts queued CR to LF.
        # Acquire on first read, so startup/history loading retain signal handling.
        with ExitStack() as scope:
            self._input_scope, self._input_raw = scope, False
            try:
                yield
            finally:
                self._input_scope = None
                self._input_raw = False

    @remember_display
    def _show_submitted_input(self, message: str) -> None:
        self._console.print(Text.assemble(("› ", "cyan bold"), visible_terminal_text(message)))

    def replay_history(self, items) -> None:
        replay_history(self, items)

    def try_overlay_approval(self, request):
        if self._model_menu is None:
            return None
        if (
            self._last_composer_activity is not None
            and monotonic() < self._last_composer_activity + 1.0
        ):
            return None
        return self._model_menu.offer(self.read_elicitation, request)

    async def wait_for_approval_idle(self):
        """Let ongoing composer edits settle before transferring approval focus."""
        while self._last_composer_activity is not None:
            remaining = self._last_composer_activity + 1.0 - monotonic()
            if remaining <= 0:
                return
            await asyncio.sleep(remaining)

    async def read_elicitation(self, request):
        self._transcript.modal_depth += 1

        async def read(label):
            with patch_stdout(raw=True), enhanced_keyboard(self._form_session.app):
                return await self._form_session.prompt_async(
                    label, key_bindings=self._form_bindings
                )

        def notice(message):
            self._console.print(Text(visible_terminal_text(message)))

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
            with patch_stdout(raw=True), enhanced_keyboard(self._form_session.app):
                return await collect_user_input(self._form_session, request)
        finally:
            self._transcript.modal_depth -= 1

    async def read_model(self, current):
        from corki.cli.menu_overlay import MenuOverlayHost
        from corki.cli.model_picker import ModelSelection, choose_model

        self._transcript.modal_depth += 1
        overlay_host = MenuOverlayHost(self._form_session)
        self._model_menu = overlay_host
        try:
            # Bundled context metadata is not evidence that a provider offers a model.
            models = tuple(info.model for info in (self._settings.model_contexts or ()))
            with patch_stdout(raw=True):
                model = await choose_model(
                    self._form_session, current.model, models, overlay_host=overlay_host
                )
                if model is None:
                    return None
                info = self._settings.model_context_info(model)
                levels = info.supported_reasoning_levels
                if not levels:
                    return ModelSelection(model)
                effort = info.reasoning_effort_for_model_switch(current.reasoning_effort)
                effort = await choose_model(
                    self._form_session,
                    effort,
                    levels,
                    title="Select reasoning effort",
                    subtitle=model,
                    allow_custom=False,
                    overlay_host=overlay_host,
                )
                if effort in {"max", "ultra"}:
                    confirmation = await choose_model(
                        self._form_session,
                        "Cancel",
                        ("Cancel", f"Use {effort}"),
                        title="Confirm reasoning effort",
                        subtitle="May increase time and token usage.",
                        allow_custom=False,
                        overlay_host=overlay_host,
                    )
                    if confirmation != f"Use {effort}":
                        return None
                return ModelSelection(model, effort) if effort is not None else None
        finally:
            overlay_host.close()
            self._model_menu = None
            self._transcript.modal_depth -= 1

    def set_model_settings(self, snapshot) -> None:
        from dataclasses import replace

        self._settings = replace(
            self._settings, model=snapshot.model, reasoning_effort=snapshot.reasoning_effort
        )
        self._session.app.invalidate()

    def set_mcp_loading(self, loading: bool) -> None:
        """Update transient discovery activity without replacing turn status/history."""
        self._mcp_loading = loading
        self._session.app.invalidate()

    def set_collaboration_mode(self, mode: str) -> None:
        self._collaboration_mode = mode
        self._session.app.invalidate()

    def set_turn_active(self, active: bool) -> None:
        if not active or not getattr(self, "_turn_active", False):
            self._active_tools = {}
        self._turn_active = active
        if status := getattr(self, "_working_status", None):
            status.set_active(active)
        if session := getattr(self, "_session", None):
            session.app.invalidate()

    def set_tool_activity(self, call_id: str, name: str | None) -> None:
        if not self._turn_active:
            return
        if name is None:
            self._active_tools.pop(call_id, None)
        else:
            self._active_tools[call_id] = name
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
        if self._turn_active:
            return self._working_fragments()
        width = max(0, self._session.app.output.get_size().columns)
        remaining = width
        fitted = []
        for style, value in to_formatted_text(self._toolbar_content()):
            if remaining <= 0:
                break
            if cell_len(value) > remaining:
                value = set_cell_size(value, remaining).rstrip()
            fitted.append((style, value))
            remaining -= cell_len(value)
        return fitted

    def _working_fragments(self):
        detail = self._hook_activity.summary
        if not detail and self._active_tools:
            detail = (
                f"Running {next(iter(self._active_tools.values()))}"
                if len(self._active_tools) == 1
                else f"Running {len(self._active_tools)} tools"
            )
        if not detail and self._reasoning_active:
            detail = self._reasoning_header or "Thinking"
        return self._working_status.fragments(
            max(0, self._session.app.output.get_size().columns),
            detail=detail,
            has_draft=bool(self._session.default_buffer.text),
        )

    def _toolbar_content(self) -> AnyFormattedText:
        if header := self._hook_activity.summary:
            header = " ".join("".join(c for c in header if c.isprintable() or c.isspace()).split())
            return [("fg:ansibrightblack", f"  {header}   ctrl+t history")]
        if self._active_tools:
            if len(self._active_tools) == 1:
                name = visible_terminal_text(next(iter(self._active_tools.values())))
                activity = f"Running {' '.join(name.split())}"
            else:
                activity = f"Running {len(self._active_tools)} tools"
            return [("fg:ansibrightblack", f"  {activity}   ctrl+t history")]
        if self._reasoning_active:
            header = self._reasoning_header or "Thinking"
            # Provider text is a literal, single-line label, never terminal markup.
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
            label = "  Plan mode"
            history = "   ctrl+t history"
            if len(label + history) <= self._session.app.output.get_size().columns:
                return [("fg:ansicyan", label), ("", history)]
            return [("fg:ansicyan", label)]
        width = self._session.app.output.get_size().columns
        hints = (
            "  ? for shortcuts   ctrl+t history   ctrl+c to quit",
            "  ? for shortcuts   ctrl+t history",
            "  ? help   ctrl+t history",
            "  ? help",
            "?",
        )
        return [("fg:ansibrightblack", next((hint for hint in hints if len(hint) <= width), ""))]

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
            Text.assemble(("• ", "red"), visible_terminal_text(message))
            if is_error
            else AssistantBlock(message)
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
        self._console.print(
            visible_terminal_text(text), end="", markup=False, highlight=False, soft_wrap=True
        )

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

    async def interrupt_assistant_message(self) -> None:
        # Finalize only the received display source, not the runtime turn. The
        # caller still emits its failure/cancellation/retry notice afterwards.
        await self.complete_assistant_message(self._transcript.assistant_stream_text())

    def abandon_assistant_stream(self) -> None:
        """Close display state from source when the terminal cannot be written."""

        text = self._transcript.assistant_stream_text()
        self._transcript.complete_assistant(text)
        self._assistant_pending = ""
        self._assistant_started = False
        self._table_source = None
        self._stream_markdown = None
        self._tail_cache = None

    async def complete_assistant_message(self, text: str) -> None:
        if getattr(self, "_animation_enabled", False):
            await self.commit_stream_tick(finish=True)
        rendered = False
        stream = self._stream_markdown
        if (
            stream is not None
            and self._table_source is not None
            and not self._table_source.tail
            and not self._transcript.stream_reflowed
            and self._transcript.assistant_stream_is_contiguous()
            and text == self._transcript.assistant_stream_text()
        ):

            def finish():
                nonlocal rendered
                rendered = stream.finish_matching_prefix(self._console, text)
                if rendered:
                    self._assistant_started |= bool(stream.emitted)

            await commit_delta(self, "\n", operation=finish)
        if rendered:
            self._assistant_pending = ""
        pending = self._assistant_pending
        if pending and (
            not self._console.is_terminal or (not self._assistant_started and text == pending)
        ):
            # Pipes keep their append-only representation. A short, plain TTY
            # answer with no committed lines can finish without a full repair.
            with self._history_view.capture_output():
                self._write_assistant_stream(pending)
        self.end_assistant_message()
        if self._transcript.complete_assistant(text, already_rendered=rendered):
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
            visible_terminal_text(delta),
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

        name = visible_terminal_text(name)
        preview = visible_terminal_text(arguments_preview.replace("\n", " "))
        if len(preview) > 180:
            preview = preview[:177] + "..."
        self._console.print(Text.assemble(("• ", "cyan"), (name, "bold"), (f" {preview}", "dim")))

    @remember_display
    def show_tool_output(self, output: str) -> None:
        """Display bounded evidence returned by a tool."""

        if output:
            self._console.print(
                visible_terminal_text(output),
                style="dim",
                markup=False,
                highlight=False,
                end="" if output.endswith("\n") else "\n",
            )

    @remember_display
    def show_tool_completed(self, name: str, *, is_error: bool) -> None:
        """Make tool failure visible without duplicating successful output."""

        if is_error:
            self._console.print(Text(f"  {visible_terminal_text(name)} failed", style="red"))

    async def commit_tool_display(self, action) -> None:
        """Commit a tool event before a later answer can overtake its output."""
        await commit_delta(self, "\n", operation=action)

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
                Text(visible_terminal_text(explanation.strip()), style="dim italic"),
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
            text = Text(visible_terminal_text(item["step"]), style=style)
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
        self._console.print(Text(visible_terminal_text(message)), style="dim")
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
