"""A queued Turn must not block read-only history observation of active work."""

import asyncio

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id
from corki.sessions import TurnStatus


@pytest.mark.parametrize("snapshot", [False, True, "page", "turn_page"])
def test_history_read_does_not_wait_behind_queued_turn_admission(tmp_path, snapshot):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        class Model:
            calls = 0

            async def stream(self, request):
                self.calls += 1
                if self.calls == 1:
                    entered.set()
                    await release.wait()
                yield ModelCompleted(
                    (
                        AssistantMessageItem(
                            "done",
                            request.items[-1].turn_id,
                            new_step_id(),
                        ),
                    )
                )

            async def aclose(self):
                pass

        model = Model()
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(tmp_path, skills_enabled=False, plugins_enabled=False),
            model=model,
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
        )

        async def consume(text):
            return [event async for event in runtime.stream(text)]

        first = asyncio.create_task(consume("first"))
        tasks = [first]
        try:
            await asyncio.wait_for(entered.wait(), 2)
            second = asyncio.create_task(consume("queued"))
            tasks.append(second)

            async def admission_started():
                while not runtime._turn_lock.locked():
                    await asyncio.sleep(0)

            await asyncio.wait_for(admission_started(), 2)
            read = runtime.load_display_snapshot if snapshot else runtime.load_display_history
            if snapshot == "page":
                read = runtime.load_display_items_page
            elif snapshot == "turn_page":
                read = runtime.load_display_turns_page
            reading = asyncio.create_task(read())
            tasks.append(reading)
            history = await asyncio.wait_for(asyncio.shield(reading), 1)
            if snapshot != "turn_page":
                items = history.items if snapshot else history
                assert [i.content for i in items if isinstance(i, UserMessageItem)] == ["first"]
            if snapshot is True or snapshot == "turn_page":
                assert len(history.turns) == 1 and history.turns[0].status is TurnStatus.RUNNING
            assert model.calls == 1 and not first.done() and not second.done()
            release.set()
            assert all(
                isinstance(events[-1], TurnCompleted)
                for events in await asyncio.gather(first, second)
            )
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())
