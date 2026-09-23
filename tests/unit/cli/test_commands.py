from pathlib import Path

from corki.cli.commands import CommandAction, CommandDispatcher
from corki.config import CorkiPaths, CorkiSettings


def dispatcher(tmp_path: Path) -> CommandDispatcher:
    settings = CorkiSettings(working_directory=tmp_path)
    return CommandDispatcher(settings, CorkiPaths.from_home(tmp_path / ".corki"))


def test_plain_message_is_not_consumed(tmp_path: Path) -> None:
    result = dispatcher(tmp_path).dispatch("please inspect this repository")

    assert result.handled is False


def test_model_selection_preserves_provider_identifier(tmp_path):
    result = dispatcher(tmp_path).dispatch("/model Vendor/Model-X")
    assert result.action.name == "MODEL"
    assert result.input_text == "Vendor/Model-X"
    assert dispatcher(tmp_path).dispatch("/model two names").action is CommandAction.NONE


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


def test_mcp_refresh_is_an_explicit_local_command(tmp_path):
    result = dispatcher(tmp_path).dispatch("/mcp refresh")
    assert result.action is CommandAction.MCP_REFRESH
    assert "not retried" in result.output


def test_memory_reset_requires_explicit_confirmation(tmp_path):
    commands = dispatcher(tmp_path)
    preview = commands.dispatch("/memory reset")
    assert preview.handled and preview.action is CommandAction.MEMORY_RESET_PREVIEW
    assert "No backup" in preview.output and "Conversations" in preview.output
    assert commands.dispatch("/memory reset confirm").action is CommandAction.MEMORY_RESET
    assert commands.dispatch("/memory reset yes").action is CommandAction.NONE


def test_memory_mode_dispatches_only_public_modes(tmp_path):
    commands = dispatcher(tmp_path)
    assert commands.dispatch("/memory mode enabled").action is CommandAction.MEMORY_MODE_ENABLED
    assert commands.dispatch("/memory mode disabled").action is CommandAction.MEMORY_MODE_DISABLED
    for text in ("/memory mode polluted", "/memory mode", "/memory mode disabled extra"):
        result = commands.dispatch(text)
        assert result.handled and result.action is CommandAction.NONE
        assert "Unknown command" in result.output
