"""Native hook completion text excludes quiet success and model-only context."""

from io import StringIO

import pytest
from rich.console import Console

from corki.cli.hook_output import hook_output_lines
from corki.cli.terminal import TerminalUI
from corki.cli.transcript import Transcript
from corki.protocol.events import HookOutputEntry, HookRunSummary


@pytest.mark.parametrize("status", ["running", "completed"])
def test_transient_and_context_only_success_leave_no_history(status):
    run = HookRunSummary(
        "id", "key", "Stop", status, entries=(HookOutputEntry("context", "secret"),)
    )
    assert hook_output_lines(run) == ()


def test_success_warning_uses_provenance_and_multiline_indent():
    run = HookRunSummary(
        "id",
        "key",
        "Stop",
        "completed",
        entries=(
            HookOutputEntry("warning", "hello\nworld"),
            HookOutputEntry("context", "secret"),
        ),
    )
    assert hook_output_lines(run) == ("↳ Hook · hello", "    world")


@pytest.mark.parametrize(
    ("status", "title"),
    [
        ("failed", "Hook failed"),
        ("blocked", "Blocked by hook"),
        ("stopped", "Hook stopped"),
    ],
)
def test_non_success_status_survives_even_without_warning(status, title):
    run = HookRunSummary(
        "id", "key", "Stop", status, entries=(HookOutputEntry("feedback", "check\nagain"),)
    )
    assert hook_output_lines(run) == ("• " + title, "  └ check", "    again")


@pytest.mark.parametrize("width", [40, 100])
@pytest.mark.parametrize(
    "status,color", [("completed", 32), ("failed", 31), ("blocked", 31), ("stopped", 31)]
)
def test_terminal_hook_status_styles_only_bullet_and_replays(width, status, color, monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    output = StringIO()
    ui = TerminalUI.__new__(TerminalUI)
    ui._console = Console(file=output, width=width, force_terminal=True, color_system="standard")
    ui._transcript = Transcript(ui)
    run = HookRunSummary(
        "id",
        "key",
        "Stop",
        status,
        entries=(HookOutputEntry("feedback", "[red]literal[/red]\nnext"),),
    )
    ui.show_hook_output(run)
    rendered = output.getvalue()
    title = {
        "completed": "Hook completed",
        "failed": "Hook failed",
        "blocked": "Blocked by hook",
        "stopped": "Hook stopped",
    }[status]
    assert rendered == (f"\n\x1b[1;{color}m•\x1b[0m {title}\n  └ [red]literal[/red]\n    next\n\n")
    assert ui._transcript.render(width) == rendered
    assert len(ui._transcript.calls) == 1


def test_terminal_hook_warning_dims_only_provenance():
    output = StringIO()
    ui = TerminalUI.__new__(TerminalUI)
    ui._console = Console(file=output, width=100, force_terminal=True, color_system="standard")
    run = HookRunSummary(
        "id",
        "key",
        "Stop",
        "completed",
        entries=(HookOutputEntry("warning", "hello\nworld"), HookOutputEntry("context", "secret")),
    )
    ui.show_hook_output(run)
    assert output.getvalue() == "\n\x1b[2m↳ Hook · \x1b[0mhello\n    world\n\n"


def test_hook_output_respects_no_color(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    output = StringIO()
    ui = TerminalUI.__new__(TerminalUI)
    ui._console = Console(file=output, width=100, force_terminal=True, color_system="standard")
    ui.show_hook_output(HookRunSummary("id", "key", "Stop", "failed"))
    assert output.getvalue() == "\n\x1b[1m•\x1b[0m Hook failed\n\n"
