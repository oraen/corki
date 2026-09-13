import asyncio

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    UserMessageItem,
    new_step_id,
)
from corki.sessions import TurnRecord, TurnStatus
from corki.tools import ToolRegistry


@pytest.mark.parametrize(
    "boundary",
    ["no-checkpoint", "before-commit", "inside-transaction", "after-commit", "after-node"],
)
@pytest.mark.parametrize("remote", [False, True, "legacy"])
@pytest.mark.parametrize("with_history", [False, True])
def test_manual_compaction_cold_recovery_does_not_resample_an_installed_summary(
    tmp_path, boundary, remote, with_history
):
    async def scenario():
        requests = []
        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            provider_name="openai" if remote else None,
            api_mode="responses" if remote else "chat_completions",
            remote_compaction_v2=remote != "legacy",
        )

        class Model:
            async def stream(self, request):
                requests.append(request)
                # Legacy provider/settings combinations cannot restore a remote
                # protocol, including after a crash before the compact node.
                assert request.compaction_turn_id is None
                assert request.tools == ()
                if with_history:
                    assert any(
                        isinstance(item, UserMessageItem) and item.content == "KEEP THIS CONSTRAINT"
                        for item in request.items
                    )
                    assert any(
                        isinstance(item, AssistantMessageItem) and item.content == "OLD ANSWER"
                        for item in request.items
                    )
                else:
                    assert len(request.items) == 1
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
            original = ()
            if with_history:
                old_turn = new_turn_id()
                original = (
                    UserMessageItem("KEEP THIS CONSTRAINT", old_turn),
                    AssistantMessageItem("OLD ANSWER", old_turn, new_step_id()),
                )
                await runtime._repository.append_items(thread, original)
            await runtime._repository.save_turn(
                TurnRecord(turn, thread, TurnStatus.RUNNING, "", operation="compact")
            )
            append = runtime._repository.append_items

            async def fail_commit(thread_id, items):
                if boundary == "after-commit":
                    await append(thread_id, items)
                raise OSError("commit boundary")

            if boundary in {"before-commit", "inside-transaction", "after-commit"}:
                if boundary == "inside-transaction":
                    install = runtime._repository._append_items_in_connection

                    def partial_install(connection, thread_id, items):
                        assert isinstance(items[0], CompactionItem)
                        install(connection, thread_id, items[:1])
                        raise OSError("commit boundary inside transaction")

                    runtime._repository._append_items_in_connection = partial_install
                else:
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
            installed_count = 2 if with_history else 1
            assert tuple(stored_before[: len(original)]) == original
            assert len(stored_before) == len(original) + (
                installed_count if boundary in {"after-commit", "after-node"} else 0
            )
            await runtime.aclose()
            runtime = create(thread)
            events = [e async for e in runtime.resume_pending()]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == (
                2 if boundary in {"before-commit", "inside-transaction"} else 1
            )
            stored = await runtime._repository.load_items(thread)
            assert tuple(stored[: len(original)]) == original
            assert len(stored) == len(original) + installed_count
            marker = stored[len(original)]
            assert isinstance(marker, CompactionItem)
            assert marker.summary == "SUMMARY"
            assert marker.remote_payload_json is None
            retained = stored[len(original) + 1 :]
            assert [item.content for item in retained] == (
                ["KEEP THIS CONSTRAINT"] if with_history else []
            )
            if boundary in {"after-commit", "after-node"}:
                assert stored == stored_before
            assert [e async for e in runtime.resume_pending()] == []
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
