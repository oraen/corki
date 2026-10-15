from dataclasses import replace
from io import StringIO
from pathlib import Path

import pytest
from prompt_toolkit.formatted_text import fragment_list_to_text
from prompt_toolkit.output import ColorDepth
from rich.cells import cell_len
from rich.console import Console

from corki.cli.session_status import session_status, status_color_depth
from corki.cli.terminal import TerminalUI
from corki.config import CorkiSettings


@pytest.mark.parametrize("width", [0, 1, 5, 8, 12, 20, 40, 100])
def test_status_fits_cells_and_preserves_directory_tail(width):
    fragments = session_status("模型-name-very-long", Path("/long/路径/project"), width)
    text = fragment_list_to_text(fragments)
    assert cell_len(text) <= width
    assert "\n" not in text
    if width >= 40:
        assert text.endswith("project")
        assert any(style == "fg:#f6e2b7" and value for style, value in fragments)
        assert any(style == "fg:#abdfa7" and value for style, value in fragments)


def test_light_background_uses_codex_latte_accents():
    fragments = session_status("model", Path("/repo"), 80, light=True)
    assert ("fg:#d59030", "model") in fragments
    assert ("fg:#489a36", "/repo") in fragments


def test_iterm_truecolor_respects_explicit_preferences(monkeypatch):
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("PROMPT_TOOLKIT_COLOR_DEPTH", raising=False)
    assert status_color_depth() is ColorDepth.DEPTH_24_BIT
    monkeypatch.setenv("PROMPT_TOOLKIT_COLOR_DEPTH", "DEPTH_8_BIT")
    assert status_color_depth() is ColorDepth.DEPTH_8_BIT
    monkeypatch.setenv("NO_COLOR", "1")
    assert status_color_depth() is ColorDepth.DEPTH_1_BIT


def test_status_uses_home_shortening_and_literal_safe_text():
    text = fragment_list_to_text(session_status("model\nname", Path.home() / "project", 100))
    assert "model name" in text and "~/project" in text
    assert "\x1b" not in fragment_list_to_text(session_status("m\x1b[31m", Path("/tmp"), 100))


def test_status_updates_after_model_change_and_remains_during_work(tmp_path):
    ui = TerminalUI(
        CorkiSettings(tmp_path, model="old"), tmp_path / "history", console=Console(file=StringIO())
    )
    controls = [
        getattr(control, "text", None) for control in ui._session.layout.find_all_controls()
    ]
    assert ui._toolbar not in controls
    assert ui._session_status in controls
    assert "old" in fragment_list_to_text(ui._session_status())
    ui.set_model_settings(replace(ui._settings, model="new"))
    ui.set_turn_active(True)
    try:
        assert "new" in fragment_list_to_text(ui._session_status())
        assert "old" not in fragment_list_to_text(ui._session_status())
    finally:
        ui.set_turn_active(False)


@pytest.mark.parametrize("width", [0, 5, 12, 40, 100])
def test_model_with_effort_fits_footer(width):
    text = fragment_list_to_text(
        session_status("gpt-5.6-sol", Path("/repo"), width, reasoning_effort="medium")
    )
    assert cell_len(text) <= width
    if width >= 40:
        assert "gpt-5.6-sol medium" in text
        assert text.endswith("/repo")


def test_footer_effort_refreshes_and_does_not_inherit_previous_selection(tmp_path):
    ui = TerminalUI(
        CorkiSettings(tmp_path, model="custom", reasoning_effort="medium"),
        tmp_path / "history",
        console=Console(file=StringIO()),
    )
    assert "custom medium" in fragment_list_to_text(ui._session_status())
    ui.set_model_settings(replace(ui._settings, reasoning_effort="high"))
    assert "custom high" in fragment_list_to_text(ui._session_status())
    ui.set_model_settings(replace(ui._settings, reasoning_effort=None))
    assert "custom default" in fragment_list_to_text(ui._session_status())
