"""Task outcome is independent of retryable terminal persistence health."""

import asyncio

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelError, ModelErrorKind
from corki.protocol.events import TurnCompleted, TurnFailed, WarningEvent
from corki.protocol.items import AssistantMessageItem, new_step_id
from corki.sessions import TurnStatus
from corki.tools import ToolRegistry


@pytest.mark.parametrize("model_fails", [False, True])
@pytest.mark.parametrize("retry_available", [False, True])
def test_terminal_storage_warning_preserves_task_result(tmp_path, model_fails, retry_available):
    async def scenario():
        requests, attempts = [], []

        class Model:
            async def stream(self, request):
                requests.append(request)
                if model_fails:
                    raise ModelError(
                        "original authentication failure", kind=ModelErrorKind.AUTHENTICATION
                    )
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "warning.db",
            home_path=tmp_path / "home",
            registry=ToolRegistry(),
            model=Model(),
        )
        repository = runtime._repository
        save, retry = repository.save_turn, repository.retry_turn_terminal

        async def failed_terminal(record):
            if record.status is not TurnStatus.RUNNING:
                raise OSError("terminal persistence unavailable")
            await save(record)

        async def retry_terminal(record):
            attempts.append(record)
            if not retry_available:
                raise OSError("terminal retry unavailable")
            await retry(record)

        repository.save_turn = failed_terminal
        repository.retry_turn_terminal = retry_terminal
        try:
            events = [event async for event in runtime.stream("input")]
            terminals = [
                event for event in events if isinstance(event, (TurnCompleted, TurnFailed))
            ]
            assert len(terminals) == 1
            terminal = terminals[0]
            if model_fails:
                assert isinstance(terminal, TurnFailed)
                assert terminal.error == "original authentication failure"
                assert terminal.error_kind == ModelErrorKind.AUTHENTICATION.value
            else:
                assert isinstance(terminal, TurnCompleted) and terminal.final_answer == "done"
            warnings = [
                event
                for event in events
                if isinstance(event, WarningEvent)
                and "terminal persistence unavailable" in event.message
            ]
            assert len(warnings) == 1 and events.index(warnings[0]) < events.index(terminal)
            assert len(requests) == 1
            assert len(attempts) == (1 if retry_available else 2)
            assert bool(runtime._pending_terminals) is not retry_available
            status = await repository.load_turn_status(runtime.thread_id, terminal.turn_id)
            assert (
                status is (TurnStatus.FAILED if model_fails else TurnStatus.COMPLETED)
                if retry_available
                else status is TurnStatus.RUNNING
            )
            if not retry_available:
                with pytest.raises(OSError, match="terminal retry unavailable"):
                    await runtime.aclose()
                assert runtime._close_storage_pending and runtime._pending_terminals
        finally:
            repository.retry_turn_terminal = retry
            await runtime.aclose()

    asyncio.run(scenario())
