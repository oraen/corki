"""Question panels share input ownership but never the approval response path."""

import asyncio

import pytest
from test_request_user_input import Model, make_runtime

from corki.cli.application import CorkiApplication
from corki.config import CorkiPaths
from corki.protocol.items import ToolResultItem, UserMessageItem


@pytest.mark.parametrize("outcome", ["answer", "interrupt", "external_cancel", "panel_error"])
def test_question_panel_owns_input_and_closes_with_real_runtime(tmp_path, outcome):
    async def scenario():
        ordinary, panel, release, closed = (asyncio.Event() for _ in range(4))
        readers = 0

        class HeldModel(Model):
            async def stream(self, request):
                await ordinary.wait()
                async for event in super().stream(request):
                    yield event

        class UI:
            async def append_assistant_delta_live(self, text):
                pass

            async def read_message(self):
                nonlocal readers
                readers += 1
                assert readers == 1
                ordinary.set()
                try:
                    await asyncio.Future()
                finally:
                    readers -= 1

            async def read_user_input(self, request):
                nonlocal readers
                readers += 1
                assert readers == 1
                panel.set()
                try:
                    await release.wait()
                    if outcome == "panel_error":
                        raise OSError("fixture panel failed")
                    if outcome == "interrupt":
                        return None
                    return {"answers": {"scope": {"answers": ["chosen scope"]}}}
                finally:
                    readers -= 1
                    closed.set()

            def __getattr__(self, name):
                if name.startswith(("show_", "begin_", "end_", "append_", "set_")):
                    return lambda *args, **kwargs: None
                raise AttributeError(name)

        model = HeldModel()
        runtime = await make_runtime(tmp_path, model, collaboration_mode="plan")
        app = CorkiApplication(
            runtime._settings, CorkiPaths.from_home(tmp_path / "home"), runtime, UI()
        )
        work = asyncio.create_task(app._consume_realtime_turn("ask"))
        try:
            await asyncio.wait_for(panel.wait(), 5)
            assert not work.done()
            if outcome == "external_cancel":
                await runtime.cancel_active()
            else:
                release.set()
            result = await asyncio.wait_for(asyncio.gather(work, return_exceptions=True), 5)
            assert result[0] is None or isinstance(result[0], asyncio.CancelledError)
            assert closed.is_set() and readers == 0
            assert app._input._modals == 0 and app._input._ordinary.is_set()
            history = await runtime._repository.load_items(runtime.thread_id)
            assert [i.content for i in history if isinstance(i, UserMessageItem)] == ["ask"]
            if outcome in ("answer", "panel_error"):
                result = next(
                    i for i in reversed(model.requests[1].items) if isinstance(i, ToolResultItem)
                )
                assert result.is_error == (outcome == "panel_error")
            else:
                assert len(model.requests) == 1
        finally:
            release.set()
            if not work.done():
                work.cancel()
            await asyncio.gather(work, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())
