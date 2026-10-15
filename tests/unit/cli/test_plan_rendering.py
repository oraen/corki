"""Plan text is data, never terminal markup; rendered states follow Codex plan cells."""

import asyncio
from io import StringIO

import pytest
from rich.console import Console

from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.cli.wrapping import is_url_token
from corki.protocol.events import PlanUpdated
from corki.protocol.ids import new_thread_id, new_turn_id


@pytest.mark.parametrize("width", [40, 100])
def test_plan_labels_and_tool_errors_preserve_literal_markup(width):
    stream = StringIO()
    ui = TerminalUI.__new__(TerminalUI)
    ui._console = Console(file=stream, width=width, color_system=None)
    ui.show_plan(
        (
            {"step": "[red]literal[/red]", "status": "completed"},
            {"step": "Check current state", "status": "in_progress"},
            {"step": "Verify remaining work", "status": "pending"},
        )
    )
    ui.show_tool_completed("[red]tool[/red]", is_error=True)
    result = stream.getvalue()
    assert "[red]literal[/red]" in result
    assert "[red]tool[/red] failed" in result
    assert "• Updated Plan" in result
    assert "  └ ✔ [red]literal[/red]" in result
    assert "    □ Check current state" in result
    assert "    □ Verify remaining work" in result


def test_plan_wrapped_lines_keep_step_indent():
    stream = StringIO()
    ui = TerminalUI.__new__(TerminalUI)
    ui._console = Console(file=stream, width=30, color_system=None)
    ui.show_plan(
        (
            {
                "step": "Inspect current files and verify the remaining implementation carefully",
                "status": "in_progress",
            },
        )
    )
    lines = stream.getvalue().splitlines()
    assert len(lines) > 2
    assert lines[1].startswith("  └ □ ")
    assert all(line.startswith("      ") for line in lines[2:])


def test_plan_url_does_not_discard_source_leading_spaces():
    stream = StringIO()
    ui = TerminalUI.__new__(TerminalUI)
    ui._console = Console(file=stream, width=100, color_system=None)
    ui.show_plan(({"step": "   Inspect https://example.test/path", "status": "pending"},))
    assert stream.getvalue() == ("• Updated Plan\n  └ □    Inspect https://example.test/path\n")


def test_plan_explanation_precedes_steps_without_interpreting_markup():
    stream = StringIO()
    ui = TerminalUI.__new__(TerminalUI)
    ui._console = Console(file=stream, width=100, color_system=None)
    ui.show_plan(
        ({"step": "Inspect files", "status": "pending"},),
        explanation="  [red]Keep original text[/red]  ",
    )
    assert stream.getvalue() == (
        "• Updated Plan\n  └ [red]Keep original text[/red]\n    □ Inspect files\n"
    )


def test_application_routes_plan_explanation_to_terminal():
    stream = StringIO()
    ui = TerminalUI.__new__(TerminalUI)
    ui._console = Console(file=stream, width=100, color_system=None)
    app = CorkiApplication.__new__(CorkiApplication)
    app._ui = ui

    async def events():
        yield PlanUpdated(new_thread_id(), new_turn_id(), (), explanation="No steps remain.")

    asyncio.run(app._render_events(events()))
    assert stream.getvalue() == ("• Updated Plan\n  └ No steps remain.\n    (no steps provided)\n")


@pytest.mark.parametrize(
    "address",
    [
        "https://example.test/api/projects/alpha/releases/2026/artifacts/report",
        "example.test/api/projects/alpha/releases/2026/artifacts/report",
        "localhost:3000/api/projects/alpha/releases/2026/artifacts/report",
    ],
)
def test_plan_preserves_long_url_tokens(address):
    stream = StringIO()
    ui = TerminalUI.__new__(TerminalUI)
    ui._console = Console(file=stream, width=30, color_system=None)
    ui.show_plan(({"step": f"Inspect {address} before continuing", "status": "in_progress"},))
    lines = stream.getvalue().splitlines()
    assert sum(address in line for line in lines) == 1
    assert any("before continuing" in line for line in lines)


def test_plan_long_non_url_word_still_wraps_next_to_url():
    stream = StringIO()
    ui = TerminalUI.__new__(TerminalUI)
    ui._console = Console(file=stream, width=30, color_system=None)
    ui.show_plan(({"step": "https://example.test/path " + "x" * 80, "status": "pending"},))
    lines = stream.getvalue().splitlines()
    assert "x" * 80 not in stream.getvalue()
    assert sum(line.count("x") for line in lines) == 81


@pytest.mark.parametrize("prefix, expected", [("9", False), ("0", True)])
def test_url_detection_handles_unbounded_numeric_host_without_integer_conversion(prefix, expected):
    assert is_url_token(prefix * 5000 + "1.2.3.4/path") is expected
