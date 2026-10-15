import io
import json
from types import SimpleNamespace

import pytest
from rich.console import Console

from corki.cli.terminal import TerminalUI
from corki.cli.tool_activity import Activity, classify, summary_rows


@pytest.mark.parametrize(
    "command,expected",
    [
        ("cat '中文 文件.md'", (Activity("Read", "中文 文件.md"),)),
        ("sed -n '1,20p' src/main.py", (Activity("Read", "src/main.py"),)),
        ("head -n 40 main.py", (Activity("Read", "main.py"),)),
        ("rg --files src", (Activity("List", "src"),)),
        ("rg -n -g '*.py' TODO src", (Activity("Search", "TODO in src"),)),
        ("ls -la src", (Activity("List", "src"),)),
        ("cat a; cat b", (Activity("Read", "a"), Activity("Read", "b"))),
        ("cat a && cat a", (Activity("Read", "a"),)),
        ("cd '中文 目录' && cat a", (Activity("Read", "a"),)),
        ("cd -- -weird && cat a", (Activity("Read", "a"),)),
        ("rg --files | head -n 50", (Activity("List", "rg --files"),)),
        ("cat a | sed -n '1,20p'", (Activity("Read", "a"),)),
        ("rg foo src | sort -u | head -20", (Activity("Search", "foo in src"),)),
        ("rg foo src || true", (Activity("Search", "foo in src"),)),
        ("git grep TODO src", (Activity("Search", "TODO in src"),)),
        ("git ls-files", (Activity("List", "git ls-files"),)),
    ],
)
def test_command_display_classification(command, expected):
    assert classify("exec_command", json.dumps({"cmd": command})) == expected


@pytest.mark.parametrize(
    "command",
    [
        "git status",
        "cat a; rm b",
        "cat a > b",
        "cat $(touch x)",
        "sed -i 's/a/b/' file",
        "cat a | python run.py",
        "rg --pre=script foo src",
        "cat a &",
        "cat a\nrm b",
        "cat 'unclosed",
        "cat a b",
        "head -n",
        "cat a &&",
        "rg --files | head -n 20 | unknown",
        "rg -l TODO | xargs rm",
        "cat a | sort -o output",
        "cat a | tee output",
        "cd a | cat b",
        "cd a b && cat c",
        "cat a | sed '1w output'",
    ],
)
def test_unknown_or_mixed_command_does_not_hide_execution(command):
    assert classify("exec_command", json.dumps({"cmd": command})) == ()


def test_bad_preview_and_unrelated_tools_remain_raw():
    assert classify("exec_command", '{"cmd": "cat ...') == ()
    assert classify("extension", '{"cmd": "cat secret"}') == ()


def test_read_coalescing_keeps_search_boundaries():
    rows = summary_rows(
        (
            Activity("Read", "a"),
            Activity("Read", "b"),
            Activity("Read", "a"),
            Activity("Search", "pattern"),
            Activity("Read", "a"),
        )
    )
    assert rows == (Activity("Read", "a, b"), Activity("Search", "pattern"), Activity("Read", "a"))


def test_collapsed_semantic_header_and_expanded_original():
    ui = TerminalUI.__new__(TerminalUI)
    stream = io.StringIO()
    ui._console = Console(file=stream, width=35, color_system=None)
    ui._transcript = SimpleNamespace(expand_tools=False)
    preview = json.dumps({"cmd": "cat a; cat b"})
    TerminalUI.show_tool_started.__wrapped__(ui, "exec_command", preview)
    assert "Exploring" in stream.getvalue()
    assert "Read a, b" in stream.getvalue()
    stream.seek(0)
    stream.truncate()
    ui._transcript.expand_tools = True
    TerminalUI.show_tool_started.__wrapped__(ui, "exec_command", preview)
    assert "exec_command" in stream.getvalue() and "Exploring" not in stream.getvalue()
