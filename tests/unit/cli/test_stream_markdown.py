from io import StringIO

import pytest
from rich.console import Console
from rich.text import Text

from corki.cli.terminal import TerminalUI
from corki.config import CorkiSettings


def test_complete_source_lines_are_rendered_before_final_answer(tmp_path):
    output = StringIO()
    ui = TerminalUI(
        CorkiSettings(tmp_path),
        tmp_path / "history",
        console=Console(file=output, force_terminal=True, width=40),
    )
    ui.begin_assistant_message()
    ui.append_assistant_delta("**bold** and `code`\n")
    visible = Text.from_ansi(output.getvalue()).plain
    assert "bold and code" in visible
    assert "**" not in visible and "`" not in visible
    ui.append_assistant_delta("second line\n")
    visible = Text.from_ansi(output.getvalue()).plain
    assert visible.count("bold and code") == 1
    assert visible.count("second line") == 1
    ui.append_assistant_delta("unfinished")
    assert "unfinished" not in output.getvalue()
    ui.end_assistant_message()


@pytest.mark.parametrize("intro", ["", "Intro\n\n"])
def test_stream_fence_context_is_not_reset_for_each_delta(tmp_path, intro):
    output = StringIO()
    ui = TerminalUI(
        CorkiSettings(tmp_path),
        tmp_path / "history",
        console=Console(file=output, force_terminal=True, width=40),
    )
    ui.begin_assistant_message()
    for delta in (intro + "```text\n", "**literal**\n", "```\n\n", "**outside**\n"):
        ui.append_assistant_delta(delta)
    visible = Text.from_ansi(output.getvalue()).plain
    assert "```" not in visible
    assert visible.count("**literal**") == 1
    assert "**outside**" not in visible and "outside" in visible
