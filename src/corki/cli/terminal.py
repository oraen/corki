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
from prompt_toolkit.history import DummyHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import ConditionalContainer, HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
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
from corki.cli.composer_paste import ComposerPaste
from corki.cli.composer_validation import ComposerValidator
from corki.cli.display_text import visible_terminal_text
from corki.cli.draft_history import DraftEntry, DraftHistory, ExpandedFileHistory
from corki.cli.elicitation import collect_elicitation
from corki.cli.history import replay_history
from corki.cli.history_view import HistoryView
from corki.cli.hook_activity import HookActivity
from corki.cli.hook_output import hook_output_text
from corki.cli.inline_images import ImageDraft
from corki.cli.input_owner import (
    BacktrackInput,
    CycleModeInput,
    DraftText,
    ImageInput,
    QueuedInput,
    input_attachments,
    input_draft,
    input_preview,
    submission_text,
)
from corki.cli.keyboard import enhanced_keyboard
from corki.cli.markdown import AssistantBlock
from corki.cli.notifications import TerminalNotifications
from corki.cli.pending_input import pending_input_lines
from corki.cli.plan_stream import PlanStreamUI
from corki.cli.proposed_plan import ProposedPlanBlock
from corki.cli.reference_completion import accept_reference
from corki.cli.session_status import session_status, status_color_depth
from corki.cli.stream_animation import ChunkingPolicy
from corki.cli.stream_commit import commit_delta
from corki.cli.stream_markdown import StreamMarkdown
from corki.cli.stream_table import TableStreamSource
from corki.cli.terminal_console import TerminalConsole
from corki.cli.terminal_palette import TerminalPalette
from corki.cli.terminal_responses import frame_terminal_responses
from corki.cli.terminal_title import project_title
from corki.cli.transcript import Transcript, remember_display
from corki.cli.user_input import collect_user_input
from corki.cli.working_status import WorkingStatus, format_work_summary
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
        self._inline_images = ImageDraft()
        self._draft_history = DraftHistory()
        self._image_tracking = False
        self._image_revision = 0
        self._image_syncing = False
        self._paste_anchor = None
        self._paste_task = None
        self._image_notice = ""
        self._backtrack_primed = False
        self._backtrack_has_target = False

        @self._bindings.add(
            "escape",
            filter=Condition(
                lambda: (
                    not self._turn_active
                    and not self._history_view.active
                    and not self._transcript.modal_depth
                    and not self._session.default_buffer.text
                    and not self._inline_images.elements
                    and self._session.default_buffer.complete_state is None
                )
            ),
        )
        def backtrack(event):
            if self._backtrack_primed:
                self._backtrack_primed = False
                event.app.exit(result=BacktrackInput())
            else:
                self._backtrack_primed = True
            event.app.invalidate()

        for key, direction in (("up", -1), ("c-p", -1), ("down", 1), ("c-n", 1)):

            @self._bindings.add(
                key,
                filter=Condition(
                    lambda: (
                        not self._history_view.active
                        and self._session.layout.current_buffer is self._session.default_buffer
                        and self._session.default_buffer.complete_state is None
                    )
                ),
            )
            def recall_draft(event, direction=direction):
                buffer = event.current_buffer
                if not self._draft_history.should_navigate(buffer.text, buffer.cursor_position):
                    if direction < 0:
                        buffer.cursor_up()
                    else:
                        buffer.cursor_down()
                    return
                if self._paste_task is not None and not self._paste_task.done():
                    return
                entry = self._draft_history.move(direction, DraftEntry.capture(self._inline_images))
                if entry is not None:
                    self._image_revision += 1
                    self._inline_images.restore(
                        entry.text, entry.images, entry.positions, entry.pastes, entry.bindings
                    )
                    self._set_image_document(len(entry.text))

        @self._bindings.add(
            Keys.BracketedPaste, filter=Condition(lambda: not self._history_view.active)
        )
        def paste_text(event):
            from corki.cli.display_text import sanitize_user_text
            from corki.cli.image_clipboard import normalize_pasted_path

            text = sanitize_user_text(event.data.replace("\r\n", "\n").replace("\r", "\n"))
            buffer = event.current_buffer
            start = buffer.cursor_position
            if len(text) > 1000:
                cursor = self._inline_images.paste(text, start, start)
                self._image_revision += 1
                self._set_image_document(cursor)
                return
            buffer.insert_text(text)
            # Keep text in the draft until decoding succeeds. Cancellation must not lose it.
            path = normalize_pasted_path(text) if 1 < len(text) <= 1000 else None
            # Codex probes image dimensions before accepting a pasted path.
            # Preflight existence before spawning our decoder: plain words must not swallow the
            # following Enter while a doomed decoder process is starting.
            if path is not None:
                try:
                    if not (self._settings.working_directory / path).is_file():
                        path = None
                except OSError:
                    path = None
            if (
                path is not None
                and getattr(event, "decode_paths", True)
                and self._settings.supports_image_input
                and len(self._images) < 8
                and (self._paste_task is None or self._paste_task.done())
            ):
                self._paste_task = event.app.create_background_task(
                    self._paste_image_path(path, text, start, self._image_revision)
                )

        @self._bindings.add(
            "c-_",
            filter=Condition(lambda: not self._history_view.active),
        )
        @self._bindings.add("c-x", "c-u", filter=Condition(lambda: not self._history_view.active))
        def undo_image_edit(event):
            cursor = self._inline_images.undo()
            if cursor is not None:
                if self._paste_anchor is not None:
                    self._paste_anchor = min(self._paste_anchor, len(self._inline_images.text))
                self._set_image_document(cursor)

        @self._bindings.add("c-v", filter=Condition(lambda: not self._history_view.active))
        @self._bindings.add("escape", "v", filter=Condition(lambda: not self._history_view.active))
        def paste_image(event):
            if self._paste_task is None or self._paste_task.done():
                self._paste_anchor = event.current_buffer.cursor_position
                self._paste_task = event.app.create_background_task(self._paste_image())

        @self._bindings.add(
            "c-d",
            filter=Condition(
                lambda: (
                    bool(self._images)
                    and not self._session.default_buffer.text
                    and not self._history_view.active
                )
            ),
        )
        def keep_image_draft(event):
            # An image-only composer is not empty: EOF must not silently discard it.
            pass

        @self._bindings.add(
            "c-c",
            filter=Condition(
                lambda: (
                    not self._history_view.active
                    and (
                        bool(self._session.default_buffer.text)
                        or bool(self._images)
                        or (self._paste_task is not None and not self._paste_task.done())
                    )
                )
            ),
        )
        def clear_composer_draft(event):
            # Codex consumes Ctrl+C on nonempty composer input before interrupt/quit.
            # Keep the cleared draft recoverable through input history, not model history.
            event.current_buffer.append_to_history()
            self._draft_history.record(DraftEntry.capture(self._inline_images))
            event.current_buffer.reset()
            self._draft = ""
            self._inline_images.clear()
            self._image_notice = ""
            if self._paste_task is not None:
                self._paste_task.cancel()

        @self._bindings.add("c-t")
        async def toggle_reasoning_history(event):
            self._history_view.open()

        @self._bindings.add("escape", "up")
        def edit_queued_input(event):
            if self._paste_task is not None and not self._paste_task.done():
                return
            if self._queue_editor is not None:
                message = self._queue_editor()
                if message is not None:
                    text = submission_text(message)
                    restored = ImageDraft()
                    restored.restore(
                        text, input_attachments(message), getattr(message, "image_positions", ())
                    )
                    snapshot = input_draft(message)
                    if snapshot is not None:
                        restored.restore(
                            snapshot.text,
                            snapshot.images,
                            snapshot.positions,
                            snapshot.pastes,
                            snapshot.bindings,
                        )
                    if self._inline_images.elements:
                        restored.append("\n")
                        restored.append(
                            self._inline_images.text,
                            self._inline_images.images,
                            self._inline_images.positions,
                            self._inline_images.pastes,
                            self._inline_images.bindings,
                        )
                    self._inline_images = restored
                    self._set_image_document(len(self._inline_images.text), event.current_buffer)

        @self._bindings.add("tab")
        def queue_submission(event):
            if self._paste_task is not None and not self._paste_task.done():
                return
            if self._complete_reference(event):
                return
            if complete_command(event.current_buffer, trailing_space=True):
                return
            self._queue_submission = True
            event.current_buffer.validate_and_handle()

        @self._bindings.add("enter")
        def submit_composer(event):
            if self._paste_task is not None and not self._paste_task.done():
                return
            if self._complete_reference(event):
                return
            complete_command(event.current_buffer)
            # A rejected Tab validation must not turn a later Enter into queue admission.
            self._queue_submission = False
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
            color_depth=status_color_depth(),
            erase_when_done=True,
            history=ExpandedFileHistory(
                str(history_file),
                lambda text: (
                    self._inline_images.expanded()[0] if text == self._inline_images.text else text
                ),
            ),
            multiline=True,
            validator=ComposerValidator(lambda: self._inline_images),
            validate_while_typing=False,
            completer=CommandCompleter(settings.working_directory),
            complete_while_typing=True,
        )
        accept = self._session.default_buffer.accept_handler

        def accept_composer(buffer):
            # Only validated user submissions belong in scrollback. A cancelled
            # realtime reader, mode switch, or empty Enter is transient UI.
            self._session.app.erase_when_done = not bool(buffer.text.strip() or self._images)
            self._draft_history.record(DraftEntry.capture(self._inline_images))
            return accept(buffer)

        self._session.default_buffer.accept_handler = accept_composer
        # Rich draft undo owns text, attachments, pastes and bindings together.
        # Do not also retain prompt-toolkit's unbounded text-only undo snapshots.
        # This affects this composer only, not buffers owned by modal dialogs.
        self._session.default_buffer.save_to_undo_stack = lambda clear_redo_stack=True: None
        self._session.default_buffer.on_text_changed += self._sync_image_text
        self._session.default_buffer.on_cursor_position_changed += self._snap_image_cursor
        self._composer_paste = ComposerPaste(self, paste_text)
        self._image_cursor = 0
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

        @self._bindings.add(
            "escape",
            filter=Condition(
                lambda: (
                    self._turn_active
                    and not self._history_view.active
                    and not self._transcript.modal_depth
                    and self._session.default_buffer.complete_state is None
                )
            ),
        )
        def interrupt_work(event):
            # Use the existing input-owner cancellation path; never spawn a
            # detached cancellation task. Keep Alt+Enter/Alt+Up chords intact
            # by deliberately not making this binding eager.
            self._draft = event.current_buffer.text
            event.app.exit(exception=KeyboardInterrupt())

        def clear_completion_dismissal(buffer):
            completer = self._session.completer
            if buffer.text != completer.dismissed_text:
                completer.dismissed_text = None

        self._session.default_buffer.on_text_changed += clear_completion_dismissal
        self._pending_inputs: tuple[str, ...] = ()
        self._table_source: TableStreamSource | None = None
        self._stream_markdown: StreamMarkdown | None = None
        self._tail_cache = None
        # Style the buffer window itself so wrapped rows and empty trailing
        # cells share the surface, without tinting completion menus/toolbars.
        self._session.layout.current_window.style = self._composer_style
        self._session.layout.current_window.height = lambda: Dimension(
            min=(
                8
                if not self._session.app.is_done
                and self._session.default_buffer.complete_state is not None
                else 1
            )
        )
        composer_visible = Condition(
            lambda: not self._session.app.is_done and not self._history_view.active
        )
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
                ConditionalContainer(Window(height=1), composer_visible),
                ConditionalContainer(
                    Window(
                        FormattedTextControl(self._image_fragments),
                        dont_extend_height=True,
                        wrap_lines=True,
                    ),
                    Condition(
                        lambda: (
                            bool(self._images or self._image_notice)
                            and not self._session.app.is_done
                            and not self._history_view.active
                        )
                    ),
                ),
                ConditionalContainer(
                    Window(height=1, style=self._composer_style), composer_visible
                ),
                self._session.layout.container,
                ConditionalContainer(
                    Window(height=1, style=self._composer_style), composer_visible
                ),
                ConditionalContainer(
                    Window(FormattedTextControl(self._session_status), height=1),
                    Condition(
                        lambda: not self._session.app.is_done and not self._history_view.active
                    ),
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
        self._notifications = TerminalNotifications(
            self._session.app.output, settings, interactive=self._console.is_terminal
        )

        @self._form_bindings.add("c-c")
        def cancel_form(event):
            if event.current_buffer.text:
                event.current_buffer.text = ""
            else:
                event.app.exit(exception=EOFError())

    @property
    def _images(self):
        return list(self._inline_images.images)

    def _set_image_document(self, cursor, buffer=None):
        self._image_syncing = True
        try:
            target = self._session.default_buffer if buffer is None else buffer
            target.document = Document(self._inline_images.text, cursor)
            self._image_cursor = cursor
        finally:
            self._image_syncing = False

    def _sync_image_text(self, buffer):
        if not self._image_tracking or self._image_syncing:
            return
        self._backtrack_primed = False
        self._image_revision += 1
        self._session.completer.reset_reference_error()
        old = self._inline_images.text
        cursor = self._inline_images.sync(buffer.text, buffer.cursor_position)
        if self._paste_anchor is not None:
            new = self._inline_images.text
            prefix = 0
            while prefix < min(len(old), len(new)) and old[prefix] == new[prefix]:
                prefix += 1
            suffix = 0
            while (
                suffix < min(len(old), len(new)) - prefix and old[-1 - suffix] == new[-1 - suffix]
            ):
                suffix += 1
            if self._paste_anchor > prefix:
                self._paste_anchor = (
                    self._paste_anchor + len(new) - len(old)
                    if self._paste_anchor >= len(old) - suffix
                    else prefix
                )
        if buffer.text != self._inline_images.text or buffer.cursor_position != cursor:
            self._set_image_document(cursor)

    def _snap_image_cursor(self, buffer):
        if not self._image_tracking or self._image_syncing:
            return
        cursor = buffer.cursor_position
        for element in self._inline_images.elements:
            if element.start < cursor < element.end:
                cursor = element.end if cursor > self._image_cursor else element.start
                break
        self._image_cursor = cursor
        if cursor != buffer.cursor_position:
            buffer.cursor_position = cursor

    def _complete_reference(self, event):
        def selected_file(path, inserted, start):
            if (
                Path(path).suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}
                and self._settings.supports_image_input
                and len(self._images) < 8
            ):
                self._paste_task = event.app.create_background_task(
                    self._paste_image_path(Path(path), inserted, start, self._image_revision)
                )

        return accept_reference(event.current_buffer, self._inline_images, on_file=selected_file)

    async def _paste_image_path(self, path, text, start, revision):
        from corki.cli.image_clipboard import read_path_image

        self._image_notice = "Reading pasted image…"
        self._session.app.invalidate()
        try:
            attachment = await read_path_image(path, self._settings.working_directory)
            buffer = self._session.default_buffer
            if revision != self._image_revision or buffer.text[start : start + len(text)] != text:
                return  # An edited/replaced draft must never receive a stale attachment.
            if (
                sum(len(image.data_url) for image in self._images) + len(attachment.data_url)
                > 44_000_000
            ):
                return
            cursor = buffer.cursor_position
            end = start + len(text)
            cursor = cursor - len(text) if cursor >= end else min(cursor, start)
            buffer.document = Document(buffer.text[:start] + buffer.text[end:], cursor)
            cursor = self._inline_images.attach(attachment, start, buffer.cursor_position)
            self._set_image_document(cursor)
            end = self._inline_images.elements[-1].end
            buffer.document = Document(
                buffer.text[:end] + " " + buffer.text[end:],
                cursor + 1 if cursor >= end else cursor,
            )
        except (OSError, ValueError, TimeoutError):
            pass  # Codex retains the original paste when it isn't a readable image.
        finally:
            self._image_notice = ""
            self._session.app.invalidate()

    async def _paste_image(self):
        from corki.cli.image_clipboard import read_clipboard_image

        if not self._settings.supports_image_input:
            self._image_notice = "This provider has image input disabled."
            self._session.app.invalidate()
            return
        if len(self._images) >= 8:
            self._image_notice = "At most 8 images per message; remove an image first."
            self._session.app.invalidate()
            return
        self._image_notice = "Reading clipboard image…"
        self._session.app.invalidate()
        try:
            attachment = await read_clipboard_image(self._settings.working_directory)
            if (
                sum(len(image.data_url) for image in self._images) + len(attachment.data_url)
                > 44_000_000
            ):
                raise ValueError("Attached images exceed the message size limit.")
            buffer = self._session.default_buffer
            position = (
                self._paste_anchor if self._paste_anchor is not None else buffer.cursor_position
            )
            cursor = self._inline_images.attach(attachment, position, buffer.cursor_position)
            self._set_image_document(cursor)
            self._image_notice = ""
        except asyncio.CancelledError:
            self._image_notice = ""
            raise
        except (OSError, ValueError, TimeoutError):
            self._image_notice = (
                "Could not paste image: clipboard empty/unavailable, image too large, or timed out."
            )
        finally:
            self._paste_anchor = None
            self._session.app.invalidate()

    def _image_fragments(self):
        text = ""
        if self._images:
            text = f"{len(self._images)} image(s) · Backspace/Delete removes an image marker"
        if self._image_notice:
            text += ("\n" if text else "") + self._image_notice
        return [("fg:ansicyan", text)]

    def _composer_style(self) -> str:
        if self._session.app.is_done:
            return ""
        # Codex style.rs::user_message_bg_rgb: lift dark backgrounds by 12%,
        # darken light backgrounds by 4%. Reuse the existing bounded OSC probe.
        background = self._terminal_palette.background
        if background is None:
            return ""
        light = self._terminal_palette.light
        top, alpha = (0, 0.04) if light else (255, 0.12)
        rgb = [int(channel * (1 - alpha) + top * alpha + 0.5) for channel in background]
        return "bg:#" + "".join(f"{channel:02x}" for channel in rgb)

    @staticmethod
    def _build_key_bindings() -> KeyBindings:
        """Create Codex-like composer controls.

        Enter submits the current buffer. Enhanced modified Enter is decoded
        as Ctrl+J; it and Escape followed by Enter insert a newline.
        prompt-toolkit's default Ctrl+C binding raises
        ``KeyboardInterrupt`` and is intentionally left intact.
        """

        bindings = KeyBindings()

        @bindings.add("c-a")
        @bindings.add("home")
        def line_start(event):
            buffer = event.current_buffer
            buffer.cursor_position += buffer.document.get_start_of_line_position()

        @bindings.add("c-e")
        @bindings.add("end")
        def line_end(event):
            buffer = event.current_buffer
            buffer.cursor_position += buffer.document.get_end_of_line_position()

        @bindings.add("c-b")
        def move_left(event):
            event.current_buffer.cursor_left()

        @bindings.add("c-f")
        def move_right(event):
            event.current_buffer.cursor_right()

        @bindings.add("c-left")
        @bindings.add("escape", "b")
        def word_left(event):
            buffer = event.current_buffer
            buffer.cursor_position += buffer.document.find_start_of_previous_word() or 0

        @bindings.add("c-right")
        @bindings.add("escape", "f")
        def word_right(event):
            buffer = event.current_buffer
            buffer.cursor_position += buffer.document.find_next_word_ending() or 0

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
            from corki.cli.terminal_input_mode import between_readers_mode

            scope.enter_context(between_readers_mode(self._session.app.input))
            self._input_raw = True
        self._queue_submission = False
        self._mode_cycle_requested = False
        self._session.completer.dismissed_text = None
        self._session.app.erase_when_done = True
        self._image_tracking = False
        if not self._draft_history.loaded:
            async for _ in self._session.history.load():
                pass
            for text in self._session.history.get_strings()[-128:]:
                self._draft_history.record(DraftEntry(text))
            self._draft_history.loaded = True
        self._draft_history.reset_navigation()
        if not self._inline_images.elements and self._inline_images.text != self._draft:
            self._inline_images.clear(self._draft)

        def track_images():
            self._image_tracking = True
            self._image_cursor = self._session.default_buffer.cursor_position

        with patch_stdout(raw=True), enhanced_keyboard(self._session.app):
            try:
                message = await self._session.prompt_async(
                    HTML("<ansicyan><b>›</b></ansicyan> "),
                    placeholder=lambda: (
                        []
                        if self._images
                        else HTML('<style fg="ansibrightblack">Ask Corki to do anything</style>')
                    ),
                    key_bindings=self._bindings,
                    default=self._draft,
                    pre_run=track_images,
                )
            except asyncio.CancelledError:
                if burst := getattr(self, "_composer_paste", None):
                    burst.finish()
                self._draft = self._session.default_buffer.text
                raise
            finally:
                if burst := getattr(self, "_composer_paste", None):
                    burst.finish()
                self._image_tracking = False
                self._history_view.close()
                self._paste_task = None  # prompt application has joined its owned background tasks
            if self._mode_cycle_requested:
                return CycleModeInput()
            if isinstance(message, BacktrackInput):
                return message
            self._draft = ""
            if message.strip():
                # PromptSession already echoed it; retain only the replay source here.
                self._transcript.calls.append((TerminalUI._show_submitted_input, (message,), {}))
            # Slash commands leave attachments in the draft, not on a command string.
            snapshot = DraftEntry.capture(self._inline_images)
            if self._inline_images.text == message:
                message, images, positions = self._inline_images.expanded()
            else:
                images, positions = tuple(self._images), self._inline_images.positions
            value = message
            command_text = message
            for number, start in sorted(enumerate(positions, 1), key=lambda p: p[1], reverse=True):
                command_text = (
                    command_text[:start] + command_text[start + len(f"[Image #{number}]") :]
                )
            if self._images and not command_text.lstrip().startswith("/"):
                value = ImageInput(
                    message, images, positions, snapshot, self._inline_images.mentions
                )
                self._inline_images.clear()
                self.show_notice(f"[Attached {len(value.attachments)} image(s)]")
            elif self._images:
                value = command_text
                self._inline_images.restore("", self._images)
                self._draft = self._inline_images.text
            else:
                if snapshot.pastes or snapshot.bindings:
                    value = DraftText(message, snapshot)
                self._inline_images.clear()
            if (
                not self._queue_submission
                and (command_text.strip() or isinstance(value, ImageInput))
                and not command_text.lstrip().startswith("/")
            ):
                self._backtrack_has_target = True
            return QueuedInput(value) if self._queue_submission else value

    @contextmanager
    def input_mode(self):
        # Keep CR distinct from Ctrl+J while switching between modal/composer
        # readers. Restoring cooked mode in those gaps converts queued CR to LF.
        # Acquire on first read, so startup/history loading retain signal handling.
        with ExitStack() as scope:
            scope.enter_context(project_title(self._console, self._settings.working_directory))
            scope.enter_context(
                self._notifications.reporting(
                    (
                        frame_terminal_responses(self._session.app.input),
                        frame_terminal_responses(self._form_session.app.input),
                    )
                )
            )
            self._input_scope, self._input_raw = scope, False
            try:
                yield
            finally:
                self._input_scope = None
                self._input_raw = False

    @remember_display
    def _show_submitted_input(self, message: str) -> None:
        if not self._transcript.replaying:
            self._backtrack_has_target = True
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

    async def read_backtrack(self, prompts, *, history=None):
        from corki.cli.backtrack_picker import choose_prompt

        if not prompts:
            self.show_notice("No previous message to edit.")
            return None
        self._transcript.modal_depth += 1
        try:
            with patch_stdout(raw=True):
                return await choose_prompt(
                    prompts if history is None else history,
                    prompts,
                    input=self._form_session.app.input,
                    output=self._form_session.app.output,
                )
        finally:
            self._transcript.modal_depth -= 1

    async def read_model(self, current):
        from corki.cli.menu_overlay import MenuOverlayHost
        from corki.cli.model_picker import choose_model_and_effort

        self._transcript.modal_depth += 1
        overlay_host = MenuOverlayHost(self._form_session)
        self._model_menu = overlay_host
        try:
            with patch_stdout(raw=True):
                return await choose_model_and_effort(
                    self._form_session, self._settings, current, overlay_host=overlay_host
                )
        finally:
            overlay_host.close()
            self._model_menu = None
            self._transcript.modal_depth -= 1

    async def read_copy(self, _request):
        from corki.cli.clipboard import copy_choices, write_clipboard
        from corki.cli.menu_overlay import MenuOverlayHost
        from corki.cli.model_picker import choose_model

        text = next(
            (
                args[0]
                for method, args, kwargs in reversed(self._transcript.calls)
                if method.__name__ in {"show_assistant_message", "show_proposed_plan"}
                and not kwargs.get("is_error", False)
                and args[0].strip()
            ),
            None,
        )
        if text is None:
            self.show_notice("No agent response to copy.")
            return
        choices = copy_choices(text)
        self._transcript.modal_depth += 1
        host = MenuOverlayHost(self._form_session)
        self._model_menu = host
        try:
            with patch_stdout(raw=True):
                selected = await choose_model(
                    self._form_session,
                    "Whole response",
                    tuple(choices),
                    title="Copy from response",
                    subtitle="Choose text to copy",
                    allow_custom=False,
                    overlay_host=host,
                )
            if selected is None:
                return
            try:
                notice = await write_clipboard(
                    choices[selected], console=self._console, cwd=self._settings.working_directory
                )
            except (OSError, ValueError):
                self.show_notice("Copy failed: clipboard unavailable or response too large.")
            else:
                self.show_notice(notice)
        finally:
            host.close()
            self._model_menu = None
            self._transcript.modal_depth -= 1

    def set_model_settings(self, snapshot) -> None:
        from dataclasses import replace

        self._settings = replace(
            self._settings, model=snapshot.model, reasoning_effort=snapshot.reasoning_effort
        )
        self._session.app.invalidate()

    def notify(self, kind, identity):
        self._notifications.notify(kind, identity)

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

    def _session_status(self) -> AnyFormattedText:
        error = self._session.completer.reference_error_for(self._session.default_buffer.document)
        if error:
            return [("fg:ansiyellow", "  " + error)]
        if (
            self._backtrack_primed
            and self._backtrack_has_target
            and not self._turn_active
            and not self._session.default_buffer.text
            and not self._inline_images.elements
        ):
            return [("fg:ansibrightblack", "  esc again to edit previous message")]
        effort = self._settings.reasoning_effort
        if effort is None:
            effort = self._settings.model_context_info(self._settings.model).default_reasoning_level
        return session_status(
            self._settings.model,
            self._settings.working_directory,
            self._session.app.output.get_size().columns,
            light=self._terminal_palette.light,
            reasoning_effort=effort if effort and effort != "none" else "default",
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
        exploration = getattr(self, "_exploration", None)
        if not detail and exploration is not None and exploration.active:
            detail = "Exploring"
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
        self._pending_inputs = tuple(input_preview(message) for message in messages)
        self._session.app.invalidate()

    def set_queue_editor(self, editor) -> None:
        self._queue_editor = editor

    def set_reference_loader(self, loader) -> None:
        self._session.completer.references = loader

    def restore_queued_inputs(self, messages: tuple[str, ...]) -> None:
        """Restore after the owned reader has joined, without submitting or writing history."""
        restored = ImageDraft()
        for message in messages:
            if restored.text:
                restored.append("\n")
            snapshot = input_draft(message)
            if snapshot is not None:
                restored.append(
                    snapshot.text,
                    snapshot.images,
                    snapshot.positions,
                    snapshot.pastes,
                    snapshot.bindings,
                )
            else:
                restored.append(
                    submission_text(message),
                    input_attachments(message),
                    getattr(message, "image_positions", ()),
                )
        if self._draft:
            if restored.text:
                restored.append("\n")
            restored.append(
                self._draft,
                self._inline_images.images,
                self._inline_images.positions,
                self._inline_images.pastes,
                self._inline_images.bindings,
            )
        self._inline_images = restored
        self._draft = restored.text

    def _pending_input_fragments(self):
        width = self._session.app.output.get_size().columns
        return [
            ("fg:ansibrightblack italic", line + "\n")
            for line in pending_input_lines(
                self._pending_inputs, width, edit_enabled=self._queue_editor is not None
            )
        ]

    def show_work_summary(self) -> None:
        self.show_work_duration(self._working_status.elapsed)

    @remember_display
    def show_work_duration(self, seconds: float | None) -> None:
        # Store duration, not a prewrapped rule: history/reflow uses its new width.
        self._console.print(Text(format_work_summary(seconds, self._console.width), style="dim"))

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

    def flush_exploration(self):
        group = getattr(self, "_exploration", None)
        if group is not None and group.calls:
            self._exploration = None
            self.show_exploration(tuple(group.calls.values()))

    @remember_display
    def show_exploration(self, calls):
        from corki.cli.tool_activity import summary_calls

        self._exploration = None
        if getattr(self._transcript, "expand_tools", False):
            return
        active = any(not c.finished or c.session_id is not None for c in calls)
        failed = any(c.failed for c in calls)
        self._console.print(
            Text("• " + ("Exploring" if active else "Explored"), style="red" if failed else "bold")
        )
        rows = summary_calls(call.activities for call in calls)
        for index, row in enumerate(rows[:32]):
            detail = Text(visible_terminal_text(row.detail))
            detail.truncate(max(1, self._console.width - 10), overflow="ellipsis")
            self._console.print(
                Text.assemble(
                    ("  └ " if index == 0 else "    ", "dim"),
                    (row.kind + " ", "cyan"),
                    detail,
                )
            )
        if len(rows) > 32:
            self._console.print("    … (ctrl+t to expand)", style="dim")
        for call in calls:
            if call.session_id is not None:
                self._console.print(Text(f"    Process running with session ID {call.session_id}"))
            if call.failed:
                code = f" (exit {call.exit_code})" if call.exit_code is not None else ""
                self._console.print(Text("    Command failed" + code, style="red"))
                self.show_tool_output.__wrapped__(self, call.output)

    @remember_display
    def show_identified_tool_started(self, call_id, name, arguments_preview):
        from corki.cli.tool_activity import Exploration, classify

        if getattr(self._transcript, "expand_tools", False):
            self.show_tool_started.__wrapped__(self, name, arguments_preview)
            return
        activities = classify(name, arguments_preview)
        group = getattr(self, "_exploration", None)
        if not activities or (group is not None and len(group.calls) >= 64):
            self.flush_exploration()
            group = None
        if activities:
            if group is None:
                group = self._exploration = Exploration()
            group.add(call_id, activities)
            if session := getattr(self, "_session", None):
                session.app.invalidate()
        else:
            self.show_tool_started.__wrapped__(self, name, arguments_preview)

    @remember_display
    def show_identified_tool_completed(
        self, call_id, name, *, is_error, exit_code=None, session_id=None
    ):
        group = getattr(self, "_exploration", None)
        if group is not None and group.complete(call_id, is_error, exit_code, session_id):
            if session_id is not None or (
                not group.active and any(c.failed for c in group.calls.values())
            ):
                self.flush_exploration()
            if session := getattr(self, "_session", None):
                session.app.invalidate()
        else:
            self.flush_exploration()
            self.show_tool_completed.__wrapped__(
                self, name, is_error=is_error or exit_code not in (None, 0)
            )

    @remember_display
    def show_tool_started(self, name: str, arguments_preview: str) -> None:
        """Render a compact tool-call header; detailed output follows separately."""

        from corki.cli.tool_activity import classify, summary_calls

        expanded = getattr(getattr(self, "_transcript", None), "expand_tools", False)
        activities = classify(name, arguments_preview) if not expanded else ()
        if activities:
            self._console.print(Text("• Exploring", style="bold"))
            rows = []
            for index, activity in enumerate(summary_calls((activities,))):
                rows.extend(
                    Text.assemble(
                        ("  └ " if index == 0 else "    ", "dim"),
                        (activity.kind + " ", "cyan"),
                        visible_terminal_text(activity.detail),
                    ).wrap(self._console, max(1, self._console.width), overflow="fold")
                )
            if len(rows) > 5:
                marker = Text(f"    … +{len(rows) - 4} lines (ctrl+t to expand)", style="dim")
                marker.truncate(max(1, self._console.width), overflow="ellipsis")
                rows = [*rows[:4], marker]
            self._console.print(Text("\n").join(rows))
            return

        name = visible_terminal_text(name)
        preview = visible_terminal_text(arguments_preview.replace("\n", " "))
        expanded = getattr(getattr(self, "_transcript", None), "expand_tools", False)
        if len(preview) > 180 and not expanded:
            preview = preview[:177] + "..."
        header = Text.assemble(("• ", "cyan"), (name, "bold"), (f" {preview}", "dim"))
        rows = header.wrap(self._console, max(1, self._console.width), overflow="fold")
        if len(rows) > 2 and not expanded:
            rows = rows[:2]
            rows[-1].truncate(max(1, self._console.width - 1), overflow="ellipsis")
        self._console.print(Text("\n").join(rows))

    @remember_display
    def show_tool_output(self, output: str) -> None:
        """Display bounded evidence returned by a tool."""

        if output:
            expanded = getattr(getattr(self, "_transcript", None), "expand_tools", False)
            rows = Text(visible_terminal_text(output).removesuffix("\n")).wrap(
                self._console, max(1, self._console.width), overflow="fold", no_wrap=False
            )
            if not expanded and len(rows) > 5:
                marker = Text(f"… +{len(rows) - 4} lines (ctrl+t to expand)")
                marker.truncate(max(1, self._console.width), overflow="ellipsis")
                rows = [*rows[:2], marker, *rows[-2:]]
            self._console.print(
                Text("\n").join(rows),
                style="dim",
                markup=False,
                highlight=False,
                end="\n",
            )

    @remember_display
    def show_tool_output_chunk(self, call_id: str, output: str, *, finished=False) -> None:
        """Cap streamed output across chunks, with bounded per-call preview state."""
        group = getattr(self, "_exploration", None)
        if group is not None and group.output(call_id, output):
            return
        self.flush_exploration()
        if getattr(self._transcript, "expand_tools", False):
            if output:
                self.show_tool_output.__wrapped__(self, output)
            return
        states = getattr(self, "_tool_previews", None)
        if states is None:
            states = self._tool_previews = {}
        count, tail = states.get(call_id, (0, []))
        for row in (
            Text(visible_terminal_text(output).removesuffix("\n")).wrap(
                self._console, max(1, self._console.width), overflow="fold", no_wrap=False
            )
            if output
            else ()
        ):
            if count < 2:
                self._console.print(row, style="dim")
            else:
                tail.append(row)
                tail = tail[-3:]
            count += 1
        if finished:
            if count > 5:
                marker = Text(f"… +{count - 4} lines (ctrl+t to expand)", style="dim")
                marker.truncate(max(1, self._console.width), overflow="ellipsis")
                self._console.print(marker)
                tail = tail[-2:]
            for row in tail:
                self._console.print(row, style="dim")
            states.pop(call_id, None)
        else:
            states[call_id] = (count, tail)

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

        self._draft_history.clear()
        self.clear_hooks()
        self._inline_images.clear()
        self._image_notice = ""
        self._console.print("\n[dim]Session ended.[/dim]")
