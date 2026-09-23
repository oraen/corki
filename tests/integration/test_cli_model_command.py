import asyncio
from io import StringIO

import pytest
from rich.console import Console
from test_thread_settings_update import Model, make_runtime, settings

from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.storage import SQLiteSessionRepository


@pytest.mark.parametrize("fail", [False, True])
def test_model_command_changes_actual_request_and_display_only_after_commit(
    tmp_path, fail, monkeypatch
):
    monkeypatch.delenv("CORKI_MODEL", raising=False)

    async def scenario():
        configured = settings(tmp_path, realtime_enabled=False)
        model = Model()
        runtime = await make_runtime(tmp_path, model, configured=configured)
        thread = runtime.thread_id
        messages = iter(["/model small", "/status", "followup"])
        notices = []

        class UI(TerminalUI):
            keep_composer_during_turn = False

            async def read_message(self):
                try:
                    return next(messages)
                except StopIteration:
                    raise EOFError from None

            def show_notice(self, text):
                notices.append(text)

        if fail:

            async def reject(**kwargs):
                raise ValueError("sensitive provider information")

            runtime.update_thread_settings = reject

        ui = UI(configured, tmp_path / "history", console=Console(file=StringIO()))
        app = CorkiApplication(configured, CorkiPaths.from_home(tmp_path), runtime, ui)
        assert await app.run() == 0
        expected = "large" if fail else "small"
        assert [request.model for request in model.requests] == [expected]
        assert ui._settings.model == expected
        assert any(f"Model:       {expected}" in text for text in notices)
        assert not any("sensitive" in text for text in notices)
        if fail:
            assert any("Model update failed" in text for text in notices)
        repository = SQLiteSessionRepository(tmp_path / "sessions.db")
        try:
            saved = repository.read_thread_model_settings(thread)
            assert saved.model == expected
        finally:
            await repository.close()
        # CLI resolves persisted settings before constructing a cold runtime;
        # an SDK caller supplying explicit settings intentionally overrides them.
        resumed = CorkiSettings.for_directory(
            tmp_path, config_file=tmp_path / "absent.toml", resume_model_settings=saved
        )
        cold = await make_runtime(tmp_path, Model(), configured=resumed, thread=thread)
        try:
            assert [event async for event in cold.resume_pending()] == []
            assert cold.thread_settings.model == expected
        finally:
            await cold.aclose()

    asyncio.run(scenario())
