import asyncio

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_turn_id
from corki.protocol.items import AssistantMessageItem, CompactionItem, UserMessageItem, new_step_id
from corki.sessions import TurnRecord, TurnStatus
from corki.tools import ToolRegistry


@pytest.mark.parametrize(
    "boundary", ["no-checkpoint", "before-commit", "after-commit", "after-node"]
)
def test_manual_compaction_cold_recovery_does_not_resample_an_installed_summary(tmp_path, boundary):
    async def scenario():
        requests = []
        settings = CorkiSettings(working_directory=tmp_path, skills_enabled=False)

        class Model:
            async def stream(self, request):
                requests.append(request)
                assert request.tools == () and len(request.items) == 1
                assert "checkpoint compaction" in request.items[-1].content
                yield ModelCompleted(
                    (AssistantMessageItem("SUMMARY", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class Sink:
            async def emit(self, event):
                pass

        def create(thread=None):
            return LangGraphRuntime.create(
                settings=settings,
                database_path=tmp_path / "sessions.db",
                model=Model(),
                registry=ToolRegistry(),
                thread_id=thread,
            )

        runtime = create()
        try:
            await runtime._ensure_ready()
            thread, turn = runtime.thread_id, new_turn_id()
            await runtime._repository.save_turn(
                TurnRecord(turn, thread, TurnStatus.RUNNING, "", operation="compact")
            )
            append = runtime._repository.append_items

            async def fail_commit(thread_id, items):
                if boundary == "after-commit":
                    await append(thread_id, items)
                raise OSError("commit boundary")

            if boundary in {"before-commit", "after-commit"}:
                runtime._repository.append_items = fail_commit
                with pytest.raises(OSError, match="commit boundary"):
                    await runtime._compiled.ainvoke(
                        _initial_state(thread, turn, settings, None),
                        config=runtime._graph_config(turn),
                        context=GraphRunContext(events=Sink()),
                    )
            elif boundary == "after-node":
                await runtime._compiled.ainvoke(
                    _initial_state(thread, turn, settings, None),
                    config=runtime._graph_config(turn),
                    context=GraphRunContext(events=Sink()),
                    interrupt_after=["compact"],
                )
            stored_before = await runtime._repository.load_items(thread)
            assert len(stored_before) == (1 if boundary in {"after-commit", "after-node"} else 0)
            await runtime.aclose()
            runtime = create(thread)
            events = [e async for e in runtime.resume_pending()]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == (2 if boundary == "before-commit" else 1)
            stored = await runtime._repository.load_items(thread)
            assert len(stored) == 1 and isinstance(stored[0], CompactionItem)
            assert not any(isinstance(i, UserMessageItem) for i in stored)
            if stored_before:
                assert stored == stored_before
            assert [e async for e in runtime.resume_pending()] == []
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
