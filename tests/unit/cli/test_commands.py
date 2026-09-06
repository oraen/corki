from pathlib import Path

from corki.cli.commands import CommandAction, CommandDispatcher
from corki.config import CorkiPaths, CorkiSettings


def dispatcher(tmp_path: Path) -> CommandDispatcher:
    settings = CorkiSettings(working_directory=tmp_path)
    return CommandDispatcher(settings, CorkiPaths.from_home(tmp_path / ".corki"))


def test_plain_message_is_not_consumed(tmp_path: Path) -> None:
    result = dispatcher(tmp_path).dispatch("please inspect this repository")

    assert result.handled is False


def test_status_reports_session_paths(tmp_path: Path) -> None:
    result = dispatcher(tmp_path).dispatch("/status")

    assert result.handled is True
    assert f"Directory:   {tmp_path}" in result.output
    assert f"Corki home:  {tmp_path / '.corki'}" in result.output


def test_clear_requests_ui_action(tmp_path: Path) -> None:
    result = dispatcher(tmp_path).dispatch("/clear")

    assert result.action is CommandAction.CLEAR


def test_question_mark_opens_shortcuts(tmp_path: Path) -> None:
    result = dispatcher(tmp_path).dispatch("?")

    assert result.handled is True
    assert "/status" in result.output


def test_unknown_command_has_actionable_output(tmp_path: Path) -> None:
    result = dispatcher(tmp_path).dispatch("/missing")

    assert result.handled is True
    assert result.output == "Unknown command: /missing"


def test_realtime_command_toggles_subsequent_turn_mode(tmp_path: Path) -> None:
    commands = dispatcher(tmp_path)

    enabled = commands.dispatch("/realtime on")
    commands.set_realtime_enabled(True)
    status = commands.dispatch("/realtime")

    assert enabled.action is CommandAction.REALTIME_ON
    assert "enabled" in status.output
