import asyncio

import pytest

from corki.cli.application import CorkiApplication, _join_realtime_tasks
from corki.cli.input_owner import InputInterrupted
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.items import AssistantMessageItem, new_step_id
from corki.tools import ToolRegistry


@pytest.mark.parametrize("failure", [OSError, EOFError, InputInterrupted])
def test_realtime_join_preserves_real_failure_without_logging_private_payload(caplog, failure):
    async def scenario():
        async def failed_owner():
            raise failure("PRIVATE_CLEANUP_DETAIL")

        task = asyncio.create_task(failed_owner())
        await asyncio.sleep(0)
        await _join_realtime_tasks(task)
        assert task.done()
        assert f"Realtime task cleanup failed: {failure.__name__}" in caplog.text
        assert "PRIVATE_CLEANUP_DETAIL" not in caplog.text

    asyncio.run(scenario())


@pytest.mark.parametrize("input_error", [KeyboardInterrupt, EOFError])
def test_normal_input_interrupt_is_not_a_realtime_cleanup_failure(tmp_path, caplog, input_error):
    async def scenario():
        started, closed = asyncio.Event(), asyncio.Event()

        class Model:
            async def stream(self, request):
                started.set()
                try:
                    await asyncio.Future()
                    yield ModelCompleted(())
                finally:
                    closed.set()

            async def aclose(self):
                pass

        class UI:
            async def read_message(self):
                await started.wait()
                raise input_error()

            def show_notice(self, message):
                pass

        settings = CorkiSettings(working_directory=tmp_path, skills_enabled=False)
        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
        )
        app = CorkiApplication(settings, CorkiPaths.from_home(tmp_path / "home"), runtime, UI())
        try:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(app._consume_turn("hello"), 2)
            assert closed.is_set()
            assert app._turn_cancelled
            assert runtime._active_run is None or runtime._active_run.done.is_set()
            assert "Realtime task cleanup failed" not in caplog.text
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["cancel", "complete", "render_error"])
@pytest.mark.parametrize("repeat_cancel", [False, True])
@pytest.mark.parametrize("cleanup_error", [False, True])
def test_realtime_application_joins_prompt_cleanup_with_actual_runtime(
    tmp_path, caplog, outcome, repeat_cancel, cleanup_error
):
    async def scenario():
        model_started, finish_model = asyncio.Event(), asyncio.Event()
        reading, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        readers, closed = [], []

        class Model:
            async def stream(self, request):
                model_started.set()
                await finish_model.wait()
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class UI:
            async def read_message(self):
                readers.append(asyncio.current_task())
                reading.set()
                try:
                    await asyncio.Future()
                finally:
                    cleaning.set()
                    await release.wait()
                    closed.append(True)
                    if cleanup_error:
                        raise OSError("PRIVATE_PROMPT_CLEANUP")

            def show_notice(self, message):
                pass

            def begin_assistant_message(self):
                if outcome == "render_error":
                    raise RuntimeError("fixture renderer failed")

            def append_assistant_delta(self, delta):
                pass

            def end_assistant_message(self):
                pass

            def show_assistant_message(self, message, *, is_error=False):
                if outcome == "render_error":
                    raise RuntimeError("fixture renderer failed")

        settings = CorkiSettings(working_directory=tmp_path, skills_enabled=False)
        runtime = LangGraphRuntime.create(
            settings=settings,
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
        )
        app = CorkiApplication(settings, CorkiPaths.from_home(tmp_path / "home"), runtime, UI())
        task = asyncio.create_task(app._consume_turn("hello"))
        try:
            await asyncio.wait_for(model_started.wait(), 1)
            await asyncio.wait_for(reading.wait(), 1)
            if outcome == "cancel":
                task.cancel()
            else:
                finish_model.set()
            await asyncio.wait_for(cleaning.wait(), 0.3)
            if repeat_cancel:
                for _ in range(3):
                    task.cancel()
                    await asyncio.sleep(0)
            assert not task.done() and not closed
            assert all(not r.done() for r in readers)
            release.set()
            if outcome == "cancel" or repeat_cancel:
                with pytest.raises(asyncio.CancelledError):
                    await task
            elif outcome == "render_error":
                with pytest.raises(RuntimeError, match="fixture renderer failed"):
                    await task
            else:
                await task
            assert closed == [True] and all(r.done() for r in readers)
            assert runtime._active_run is None or runtime._active_run.done.is_set()
            if cleanup_error:
                assert "Input task cleanup failed: OSError" in caplog.text
                assert "PRIVATE_PROMPT_CLEANUP" not in caplog.text
        finally:
            # Also clean the old implementation's orphan prompt after RED assertions.
            release.set()
            finish_model.set()
            task.cancel()
            for reader in readers:
                if not reader.done() and not reader.cancelling():
                    reader.cancel()
            await asyncio.gather(task, *readers, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())
