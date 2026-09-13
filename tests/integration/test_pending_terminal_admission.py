"""A pending old terminal does not replay work or block an independent new turn."""

import asyncio

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted, WarningEvent
from corki.protocol.items import AssistantMessageItem, new_step_id
from corki.sessions import TurnStatus
from corki.tools import ToolRegistry


def test_new_turn_continues_with_old_terminal_pending(tmp_path):
    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(
                    (
                        AssistantMessageItem(
                            f"answer {len(requests)}", request.items[-1].turn_id, new_step_id()
                        ),
                    )
                )

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "pending.db",
            home_path=tmp_path / "home",
            registry=ToolRegistry(),
            model=Model(),
        )
        repo = runtime._repository
        save, retry = repo.save_turn, repo.retry_turn_terminal

        async def unavailable_terminal(record):
            if record.status is not TurnStatus.RUNNING:
                raise OSError("old terminal unavailable")
            await save(record)

        repo.save_turn = unavailable_terminal
        repo.retry_turn_terminal = unavailable_terminal
        try:
            first = [e async for e in runtime.stream("first")]
            assert isinstance(first[-1], TurnCompleted)
            pending = dict(runtime._pending_terminals)
            prefix = await repo.load_items(runtime.thread_id)
            repo.save_turn = save
            second = [e async for e in runtime.stream("second")]
            assert isinstance(second[-1], TurnCompleted)
            assert second[-1].final_answer == "answer 2"
            assert len(requests) == 2 and runtime._pending_terminals == pending
            warnings = [
                e
                for e in second
                if isinstance(e, WarningEvent) and "old terminal unavailable" in e.message
            ]
            assert len(warnings) == 1 and warnings[0].turn_id == second[-1].turn_id
            assert second.index(warnings[0]) < second.index(second[-1])
            assert (await repo.load_items(runtime.thread_id))[: len(prefix)] == prefix
            assert (
                await repo.load_turn_status(runtime.thread_id, first[-1].turn_id)
                is TurnStatus.RUNNING
            )
            assert (
                await repo.load_turn_status(runtime.thread_id, second[-1].turn_id)
                is TurnStatus.COMPLETED
            )
            # Recovery must not mistake an owned completed task for unfinished work.
            with pytest.raises(OSError, match="old terminal unavailable"):
                _ = [e async for e in runtime.resume_pending()]
            assert len(requests) == 2
            repo.retry_turn_terminal = retry
            assert [e async for e in runtime.resume_pending()] == []
            assert not runtime._pending_terminals and len(requests) == 2
            assert await repo.confirm_turn_terminal(next(iter(pending.values())))
        finally:
            repo.save_turn, repo.retry_turn_terminal = save, retry
            await runtime.aclose()

    asyncio.run(scenario())
