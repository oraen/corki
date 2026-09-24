import io
import json

import pytest
from rich.console import Console

from corki.cli.history import replay_history
from corki.cli.terminal import TerminalUI
from corki.cli.transcript import Transcript
from corki.protocol.items import ToolCallItem, ToolResultItem, item_from_payload, item_to_payload
from corki.protocol.tools import ToolCall


def ui():
    value = TerminalUI.__new__(TerminalUI)
    value._console = Console(file=io.StringIO(), color_system=None, width=100)
    value._transcript = Transcript(value)
    return value


def test_cold_history_keeps_untruncated_source_and_isolates_active_group():
    terminal = ui()
    terminal.show_identified_tool_started("live", "exec_command", json.dumps({"cmd": "cat live"}))
    live = terminal._exploration
    body = "x" * 5000 + "\nEND_OF_FULL_HISTORY"
    replay_history(
        terminal,
        (
            ToolCallItem(ToolCall("old", "exec_command", {"cmd": "cat old"}), "turn", "step"),
            ToolResultItem("old", "exec_command", body, "turn", exit_code=0),
        ),
    )
    assert terminal._exploration is live and live.active
    assert "END_OF_FULL_HISTORY" not in terminal._console.file.getvalue()
    assert "END_OF_FULL_HISTORY" in terminal._transcript.render(100, expand_tools=True)


def test_legacy_process_outcome_is_visible_not_assumed_successful():
    terminal = ui()
    replay_history(
        terminal,
        (
            ToolCallItem(ToolCall("old", "exec_command", {"cmd": "cat absent"}), "turn", "step"),
            ToolResultItem("old", "exec_command", "Process exited with code 7\nERROR", "turn"),
        ),
    )
    assert "Process exited with code 7" in terminal._console.file.getvalue()


def test_status_roundtrip_is_optional_and_rejects_boolean():
    legacy = ToolResultItem("id", "exec_command", "body", "turn")
    assert "exit_code" not in item_to_payload(legacy)
    assert "session_id" not in item_to_payload(legacy)
    saved = ToolResultItem("id", "exec_command", "body", "turn", exit_code=7)
    restored = item_from_payload("tool_result", item_to_payload(saved))
    assert restored.exit_code == 7 and not restored.is_error
    with pytest.raises(ValueError, match="shell display"):
        ToolResultItem("id", "exec_command", "body", "turn", exit_code=True)
