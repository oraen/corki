"""Plan queue commits while the actual model awaits display, before item completion."""

import asyncio
from io import StringIO

import pytest
from rich.console import Console

from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelTextDelta
from corki.protocol.items import AssistantMessageItem, new_step_id


@pytest.mark.parametrize("burst", [False, True])
def test_plan_queue_drains_before_model_completion(tmp_path, burst):
    async def scenario():
        waiting, drained = asyncio.Event(), asyncio.Event()
        ticks = []
        body = "".join(f"- row {n}\n" for n in range(10)) if burst else "first\n\nsecond\n"
        source = "<proposed_plan>\n" + body

        class Model:
            async def stream(self, request):
                yield ModelTextDelta(source)
                waiting.set()
                await drained.wait()
                yield ModelCompleted(
                    (AssistantMessageItem(source, request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class UI(TerminalUI):
            async def commit_stream_tick(self, *, catch_up_only=False, finish=False):
                stream = getattr(self, "_plan_stream", None)
                before = stream.emitted if stream else 0
                await super().commit_stream_tick(catch_up_only=catch_up_only, finish=finish)
                if not finish and stream is not None and stream.emitted > before:
                    if not catch_up_only:
                        assert waiting.is_set()
                    ticks.append((catch_up_only, stream.emitted - before))
                    if not stream.queued_lines:
                        drained.set()

        configured = CorkiSettings(
            tmp_path, skills_enabled=False, plugins_enabled=False, collaboration_mode="plan"
        )
        runtime = await LangGraphRuntime.acreate(
            settings=configured,
            model=Model(),
            database_path=tmp_path / "s.db",
            home_path=tmp_path / "home",
        )
        output = StringIO()
        ui = UI(
            configured,
            tmp_path / "history",
            console=Console(file=output, force_terminal=True, width=40),
        )
        app = CorkiApplication(configured, CorkiPaths.from_home(tmp_path), runtime, ui)
        try:
            await asyncio.wait_for(app._consume_events(runtime.stream("Plan")), 3)
            assert ticks
            if burst:
                assert ticks == [(True, 10)]
            else:
                assert len(ticks) > 1 and set(ticks) == {(False, 1)}
            assert "Proposed Plan" in output.getvalue()
            assert getattr(ui, "_plan_stream", None) is None
            assert not ui._animation_enabled
            assert not any(t.get_name() == "corki-display-next-event" for t in asyncio.all_tasks())
        finally:
            drained.set()
            await runtime.aclose()

    asyncio.run(scenario())
