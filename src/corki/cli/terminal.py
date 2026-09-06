"""Codex-inspired terminal user interface for the CLI adapter.

Rich owns durable output written into terminal scrollback. prompt-toolkit owns
the editable composer. This division is deliberately small and mirrors the
proven approach used by interactive agent CLIs without introducing a full
screen widget framework before Corki needs one.
"""

from __future__ import annotations

from pathlib import Path

from prompt_toolkit import HTML, PromptSession
from prompt_toolkit.formatted_text import AnyFormattedText
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from corki import __version__
from corki.config import CorkiSettings


class TerminalUI:
    """Render Corki output and collect editable user messages."""

    def __init__(
        self,
        settings: CorkiSettings,
        history_file: Path,
        *,
        console: Console | None = None,
    ) -> None:
        self._settings = settings
        self._console = console or Console()
        self._bindings = self._build_key_bindings()
        self._session: PromptSession[str] = PromptSession(
            history=FileHistory(str(history_file)),
            multiline=True,
        )

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

    async def read_message(self) -> str:
        """Wait for one message while preserving asynchronous stdout safety."""

        with patch_stdout(raw=True):
            return await self._session.prompt_async(
                HTML("<ansicyan><b>›</b></ansicyan> "),
                placeholder=HTML('<style fg="ansibrightblack">Ask Corki to do anything</style>'),
                bottom_toolbar=self._toolbar,
                key_bindings=self._bindings,
            )

    @staticmethod
    def _toolbar() -> AnyFormattedText:
        return HTML('  <style fg="ansibrightblack">? for shortcuts   ctrl+c to quit</style>')

    def show_assistant_message(self, message: str, *, is_error: bool = False) -> None:
        """Render a final runtime response without coupling to runtime types."""

        marker_style = "red" if is_error else "green"
        self._console.print()
        self._console.print(Text.assemble(("• ", marker_style), message))
        self._console.print()

    def begin_assistant_message(self) -> None:
        """Start one streamed assistant block."""

        self._console.print()
        self._console.print("• ", style="green", end="")

    def append_assistant_delta(self, delta: str) -> None:
        """Append provider text immediately without Rich markup interpretation."""

        self._console.print(delta, end="", markup=False, highlight=False, soft_wrap=True)

    def end_assistant_message(self) -> None:
        """Terminate the active streamed assistant block."""

        self._console.print("\n")

    def begin_reasoning(self) -> None:
        """Start a visually separate provider reasoning stream."""

        self._console.print()
        self._console.print("◦ thinking  ", style="dim italic", end="")

    def append_reasoning_delta(self, delta: str) -> None:
        """Render reasoning softly without mixing it into assistant content."""

        self._console.print(
            delta,
            style="dim italic",
            end="",
            markup=False,
            highlight=False,
            soft_wrap=True,
        )

    def end_reasoning(self) -> None:
        """Terminate the current reasoning stream."""

        self._console.print("\n")

    def show_tool_started(self, name: str, arguments_preview: str) -> None:
        """Render a compact tool-call header; detailed output follows separately."""

        preview = arguments_preview.replace("\n", " ")
        if len(preview) > 180:
            preview = preview[:177] + "..."
        self._console.print(Text.assemble(("• ", "cyan"), (name, "bold"), (f" {preview}", "dim")))

    def show_tool_output(self, output: str) -> None:
        """Display bounded evidence returned by a tool."""

        if output:
            self._console.print(output, style="dim", markup=False, highlight=False)

    def show_tool_completed(self, name: str, *, is_error: bool) -> None:
        """Make tool failure visible without duplicating successful output."""

        if is_error:
            self._console.print(f"  {name} failed", style="red")

    def show_plan(self, plan: tuple[dict[str, str], ...]) -> None:
        """Render the latest user-visible plan."""

        self._console.print("  Plan", style="bold dim")
        markers = {"completed": "✓", "in_progress": "→", "pending": "·"}
        for item in plan:
            self._console.print(
                f"  {markers.get(item['status'], '·')} {item['step']}",
                style="dim",
            )

    def show_notice(self, message: str) -> None:
        """Render local command output in the transcript."""

        self._console.print()
        self._console.print(message, style="dim")
        self._console.print()

    def clear(self) -> None:
        """Clear terminal scrollback and restore the startup header."""

        self._console.clear()
        self.show_welcome()

    def show_goodbye(self) -> None:
        """Leave a concise, deterministic shutdown message."""

        self._console.print("\n[dim]Session ended.[/dim]")
