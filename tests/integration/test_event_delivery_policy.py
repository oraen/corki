"""Default event delivery must not make model progress depend on UI consumption."""

import asyncio
from dataclasses import replace

import pytest

from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelTextDelta
from corki.protocol.events import AssistantTextDelta, TurnCompleted, TurnStarted
from corki.protocol.items import AssistantMessageItem, new_step_id
from corki.tools import ToolRegistry


@pytest.mark.parametrize("configuration", ["sdk", "generated"])
def test_default_turn_finishes_while_event_consumer_is_paused(tmp_path, configuration):
    async def scenario():
        settings = CorkiSettings(tmp_path)
        if configuration == "generated":
            paths = CorkiPaths.from_home(tmp_path / "home")
            paths.ensure_exists()
            settings = CorkiSettings.for_directory(tmp_path, config_file=paths.config_file)
        settings = replace(settings, skills_enabled=False, plugins_enabled=False)
        source = [f"part-{index}\n" for index in range(1024)]
        closed = asyncio.Event()

        class Model:
            async def stream(self, request):
                try:
                    for part in source:
                        yield ModelTextDelta(part)
                    yield ModelCompleted(
                        (
                            AssistantMessageItem(
                                "".join(source), request.items[-1].turn_id, new_step_id()
                            ),
                        )
                    )
                finally:
                    closed.set()

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            database_path=tmp_path / "state.db",
            home_path=tmp_path / "home",
            registry=ToolRegistry(),
            model=Model(),
        )
        stream = runtime.stream("produce output")
        try:
            assert isinstance(await anext(stream), TurnStarted)
            run = runtime._active_run
            # No data event is consumed until the owned Turn has fully finished.
            await asyncio.wait_for(run.done.wait(), 3)
            assert closed.is_set()
            assert isinstance(run.result(), TurnCompleted)
            assert await runtime._repository.latest_running_turn(runtime.thread_id) is None
            await runtime.aclose()
            events = [event async for event in stream]
            assert [
                event.delta for event in events if isinstance(event, AssistantTextDelta)
            ] == source
            assert sum(isinstance(event, TurnCompleted) for event in events) == 1
            assert events[-1].final_answer == "".join(source)
        finally:
            await stream.aclose()
            await runtime.aclose()

    asyncio.run(scenario())
