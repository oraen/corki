import io
import json
from types import SimpleNamespace

from rich.console import Console

from corki.cli.terminal import TerminalUI
from corki.cli.transcript import Transcript
from corki.core.tool_display import shell_display_status
from corki.protocol.tools import CodeModeOutput


def make_ui():
    ui = TerminalUI.__new__(TerminalUI)
    ui._console = Console(file=io.StringIO(), width=80, color_system=None)
    ui._transcript = Transcript(ui)
    return ui


def start(ui, call_id, command):
    ui.show_identified_tool_started(call_id, "exec_command", json.dumps({"cmd": command}))


def test_grouping_and_reflow_preserve_live_state_and_full_output():
    ui = make_ui()
    start(ui, "a", "cat alpha")
    ui.show_tool_output_chunk("a", "FULL_A\n", finished=True)
    ui.show_identified_tool_completed("a", "exec_command", is_error=False, exit_code=0)
    start(ui, "b", "cat beta")
    ui.show_tool_output_chunk("b", "FULL_B\n", finished=True)
    live = ui._exploration
    count = len(ui._transcript.calls)
    assert "Exploring" in ui._transcript.render(40)
    assert "Read alpha, beta" in ui._transcript.render(100)
    assert ui._exploration is live and live.active
    expanded = ui._transcript.render(100, expand_tools=True)
    assert "FULL_A" in expanded and "FULL_B" in expanded
    assert "exec_command" in expanded
    assert len(ui._transcript.calls) == count
    ui.show_identified_tool_completed("b", "exec_command", is_error=False, exit_code=0)
    ui.flush_exploration()
    output = ui._console.file.getvalue()
    assert output.count("Explored") == 1
    assert "Read alpha, beta" in output and "FULL_A" not in output
    assert ui._exploration is None


def test_unknown_call_flushes_before_raw_header_without_replay_duplicates():
    ui = make_ui()
    start(ui, "a", "cat alpha")
    ui.show_identified_tool_completed("a", "exec_command", is_error=False)
    start(ui, "b", "git status")
    start(ui, "c", "cat beta")
    ui.show_identified_tool_completed("c", "exec_command", is_error=False)
    ui.flush_exploration()
    rendered = ui._transcript.render(100)
    assert rendered.index("Read alpha") < rendered.index("git status") < rendered.index("Read beta")
    assert rendered.count("Read alpha") == 1 and rendered.count("Read beta") == 1


def test_nonzero_process_status_is_not_tool_transport_failure_but_still_visible():
    ui = make_ui()
    start(ui, "a", "cat absent")
    ui.show_tool_output_chunk("a", "missing file\n", finished=True)
    ui.show_identified_tool_completed("a", "exec_command", is_error=False, exit_code=1)
    assert "Command failed (exit 1)" in ui._console.file.getvalue()
    assert "missing file" in ui._console.file.getvalue()
    assert ui._exploration is None
    expanded = ui._transcript.render(80, expand_tools=True)
    assert "failed" in expanded and "missing file" in expanded


def test_running_process_does_not_become_explored():
    ui = make_ui()
    start(ui, "a", "tail -f log")
    ui.show_identified_tool_completed("a", "exec_command", is_error=False, session_id=19)
    output = ui._console.file.getvalue()
    assert "Exploring" in output and "session ID 19" in output and "Explored" not in output


def test_shell_status_requires_typed_metadata_not_output_text():
    result = SimpleNamespace(code_mode_output=CodeModeOutput({"exit_code": 1, "session_id": True}))
    assert shell_display_status("exec_command", result) == {"exit_code": 1}
    assert shell_display_status("extension", result) == {}
    result.code_mode_output = None
    assert shell_display_status("exec_command", result) == {}


def test_interruption_never_turns_unconfirmed_calls_into_explored():
    ui = make_ui()
    start(ui, "a", "cat alpha")
    ui.show_tool_output_chunk("a", "unfinished\n")
    ui.show_notice("exec_command interrupted; completion not confirmed.")
    output = ui._console.file.getvalue()
    assert "Exploring" in output and "Explored" not in output
    assert "interrupted; completion not confirmed" in output
    assert ui._exploration is None
    assert "unfinished" in ui._transcript.render(100, expand_tools=True)


def test_group_and_error_preview_have_capacity_limits():
    ui = make_ui()
    for number in range(65):
        key = str(number)
        start(ui, key, "cat alpha")
        ui.show_tool_output_chunk(key, "x" * 9000)
        assert len(ui._exploration.calls[key].output) == 2048
        ui.show_identified_tool_completed(key, "exec_command", is_error=False)
    assert len(ui._exploration.calls) == 1
    assert ui._console.file.getvalue().count("Explored") == 1


def test_orphan_completion_cannot_complete_another_calls_exploration():
    ui = make_ui()
    start(ui, "a", "cat alpha")
    ui.show_identified_tool_completed("different", "exec_command", is_error=True)
    output = ui._console.file.getvalue()
    assert "Exploring" in output and "Explored" not in output
    assert "failed" in output


def test_mixed_call_is_not_merged_with_adjacent_read_only_calls():
    ui = make_ui()
    for key, command in (
        ("a", "cat alpha"),
        ("b", "cat beta; cat gamma; rg needle src; cat delta"),
        ("c", "cat epsilon"),
    ):
        start(ui, key, command)
        ui.show_identified_tool_completed(key, "exec_command", is_error=False, exit_code=0)
    ui.flush_exploration()
    output = ui._console.file.getvalue()
    expected = (
        "Read alpha",
        "Read beta",
        "Read gamma",
        "Search needle in src",
        "Read delta",
        "Read epsilon",
    )
    offsets = [output.index(text) for text in expected]
    assert offsets == sorted(offsets)
    assert output.count("Read ") == 5
    assert ui._transcript.render(80).count("Read ") == 5


def test_parallel_failure_waits_for_other_call_without_hiding_failure():
    ui = make_ui()
    start(ui, "a", "cat missing")
    start(ui, "b", "cat slow")
    ui.show_tool_output_chunk("a", "file missing\n", finished=True)
    ui.show_identified_tool_completed("a", "exec_command", is_error=False, exit_code=2)
    assert ui._exploration.active
    during = ui._transcript.render(80)
    assert "Exploring" in during and "Explored" not in during
    assert "Command failed (exit 2)" in during and "file missing" in during
    ui.show_identified_tool_completed("b", "exec_command", is_error=False, exit_code=0)
    assert ui._exploration is None
    output = ui._console.file.getvalue()
    assert output.count("Explored") == 1
    assert "Command failed (exit 2)" in output
