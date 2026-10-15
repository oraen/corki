import asyncio
from dataclasses import replace
from io import StringIO

import pytest
from rich.console import Console

from corki.cli.hook_activity import HookActivity
from corki.cli.terminal import TerminalUI
from corki.config import CorkiSettings
from corki.protocol.events import HookOutputEntry, HookRunSummary

RUN = HookRunSummary("run", "key", "Stop", "running", "Checking")


def test_reveal_and_quiet_linger_boundaries():
    activity = HookActivity()
    activity.start(RUN, 0)
    activity.advance(0.299)
    assert activity.summary is None
    activity.advance(0.3)
    assert activity.summary == "Checking"
    activity.complete(replace(RUN, status="completed"), 0.4)
    assert activity.summary is None
    assert activity.deadline == pytest.approx(0.9)
    activity.advance(0.899)
    assert activity.deadline is not None
    activity.advance(0.9)
    assert activity.deadline is None


@pytest.mark.parametrize("completed_at", [0.299, 1.0])
def test_quiet_success_never_revealed_does_not_flash(completed_at):
    activity = HookActivity()
    activity.start(RUN, 0)
    activity.complete(replace(RUN, status="completed"), completed_at)
    activity.advance(2)
    assert activity.summary is activity.deadline is None


def test_delayed_reveal_uses_actual_visibility_time_and_duplicate_resets():
    activity = HookActivity()
    activity.start(RUN, 0)
    activity.start(RUN, 1)
    activity.advance(1.299)
    assert activity.summary is None
    activity.advance(2)
    activity.complete(replace(RUN, status="completed"), 2.1)
    assert activity.deadline == 2.6
    activity.clear()
    assert activity.summary is activity.deadline is None


@pytest.mark.parametrize(
    "messages,expected",
    [
        ((" Check ", "Check"), "Check"),
        (("Check", "Other"), "Running hooks"),
        ((None, ""), "Running hooks"),
        ((" ",), "Running hook"),
    ],
)
def test_status_aggregation(messages, expected):
    activity = HookActivity()
    for index, message in enumerate(messages):
        activity.start(replace(RUN, id=str(index), status_message=message), 0)
    activity.advance(0.3)
    assert activity.summary == expected


@pytest.mark.parametrize(
    "status,entries",
    [
        ("failed", ()),
        ("blocked", ()),
        ("stopped", ()),
        ("completed", (HookOutputEntry("warning", "diagnostic"),)),
    ],
)
def test_nonquiet_completion_does_not_linger(status, entries):
    activity = HookActivity()
    activity.start(RUN, 0)
    activity.advance(0.3)
    completed = replace(RUN, status=status, entries=entries)
    activity.complete(completed, 0.4)
    activity.complete(completed, 0.5)  # Missing begin is harmless.
    assert activity.summary is activity.deadline is None


def test_context_only_success_and_already_visible_minimum():
    activity = HookActivity()
    activity.start(RUN, 0)
    activity.advance(0.3)
    activity.complete(
        replace(RUN, status="completed", entries=(HookOutputEntry("context", "hidden"),)), 0.4
    )
    assert activity.summary is None and activity.deadline is not None
    activity.start(RUN, 1)
    activity.advance(1.3)
    activity.complete(replace(RUN, status="completed"), 2)
    assert activity.summary is activity.deadline is None


def test_terminal_owns_one_timer_and_no_persistent_output(tmp_path, monkeypatch):
    asyncio.run(_terminal_timer_scenario(tmp_path, monkeypatch))


async def _terminal_timer_scenario(tmp_path, monkeypatch):
    now = [0.0]
    monkeypatch.setattr("corki.cli.terminal.monotonic", lambda: now[0])
    output = StringIO()
    ui = TerminalUI(CorkiSettings(tmp_path), tmp_path / "history", console=Console(file=output))
    ui.hook_started(RUN)
    first = ui._hook_timer
    ui.hook_started(RUN)
    assert first.cancelled()
    now[0] = 0.3
    ui._refresh_hooks()
    assert ui._hook_timer is None
    assert "Checking" in str(ui._toolbar())
    ui.hook_completed(replace(RUN, status="completed"))
    assert ui._hook_activity.summary is None
    linger = ui._hook_timer
    assert linger is not None
    ui.clear_hooks()
    assert linger.cancelled() and ui._hook_timer is None
    assert ui._transcript.calls == [] and output.getvalue() == ""
    assert not (tmp_path / "history").exists()
