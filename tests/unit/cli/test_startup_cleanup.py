"""Display startup failure must release the runtime already owned by the CLI."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from corki.cli.application import CorkiApplication


@pytest.mark.parametrize("stage", ["welcome", "resize"])
def test_display_startup_failure_closes_owned_runtime(stage):
    error = OSError("terminal disconnected")
    ui = SimpleNamespace(show_welcome=Mock(), show_goodbye=Mock(), watch_resize=Mock())
    if stage == "welcome":
        ui.show_welcome.side_effect = error
    else:
        ui.watch_resize.side_effect = error
    # Exercise the lifecycle boundary without starting a model or native tool.
    application = object.__new__(CorkiApplication)
    application._ui = ui
    application._runtime = SimpleNamespace(aclose=AsyncMock())
    application._mcp_inventory = SimpleNamespace(aclose=AsyncMock())
    application._history_pager = None
    with pytest.raises(OSError) as caught:
        asyncio.run(application.run())
    assert caught.value is error
    application._runtime.aclose.assert_awaited_once()
    application._mcp_inventory.aclose.assert_awaited_once()
    ui.show_goodbye.assert_called_once()


@pytest.mark.parametrize("stage", ["factory", "enter", "exit"])
def test_terminal_mode_failure_closes_runtime_exactly_once(stage):
    error = OSError("console mode unavailable")
    mode = MagicMock()
    mode.__exit__.return_value = False
    ui = SimpleNamespace(input_mode=Mock(return_value=mode))
    application = object.__new__(CorkiApplication)
    application._ui = ui
    application._runtime = SimpleNamespace(aclose=AsyncMock())

    async def owned_run():
        await application._runtime.aclose()
        return 0

    application._run = AsyncMock(side_effect=owned_run)
    if stage == "factory":
        ui.input_mode.side_effect = error
    elif stage == "enter":
        mode.__enter__.side_effect = error
    else:
        mode.__exit__.side_effect = error
    with pytest.raises(OSError) as caught:
        asyncio.run(application.run())
    assert caught.value is error
    application._runtime.aclose.assert_awaited_once()
    assert application._run.await_count == (1 if stage == "exit" else 0)
