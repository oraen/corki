"""A cancelled Turn must own a started compatibility patch worker until it exits."""

import asyncio
import threading

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCancelled
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import ToolCallItem, new_step_id
from corki.protocol.tools import ToolCall


@pytest.mark.parametrize("ending", ["cancel", "close"])
@pytest.mark.parametrize("worker_fails", [False, True])
def test_cancelled_turn_joins_legacy_patch_worker_before_terminal(
    tmp_path, monkeypatch, ending, worker_fails
):
    async def scenario():
        import corki.tools.builtin.patch as patch_module

        entered = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        original = patch_module._apply_operations

        def controlled(cwd, operations):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5), "test did not release patch worker"
            if worker_fails:
                raise OSError("patch worker failed after cancellation")
            return original(cwd, operations)

        monkeypatch.setattr(patch_module, "_apply_operations", controlled)

        class Model:
            async def stream(self, request):
                turn = request.items[-1].turn_id
                yield ModelCompleted(
                    (
                        ToolCallItem(
                            ToolCall(
                                new_tool_call_id(),
                                "apply_patch",
                                {
                                    "patch": "*** Begin Patch\n*** Add File: created.txt\n"
                                    "+written\n*** End Patch"
                                },
                            ),
                            turn,
                            new_step_id(),
                        ),
                    )
                )

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(tmp_path, skills_enabled=False, execution_permissions=None),
            model=Model(),
            database_path=tmp_path / "state.db",
            home_path=tmp_path / "home",
        )
        events = []

        async def consume():
            try:
                async for event in runtime.stream("write file"):
                    events.append(event)
            except asyncio.CancelledError:
                pass

        consumer = asyncio.create_task(consume())
        closer = None
        try:
            await asyncio.wait_for(entered.wait(), 5)
            if ending == "cancel":
                await runtime.cancel_active()
            else:
                closer = asyncio.create_task(runtime.aclose())
            await asyncio.sleep(0.05)
            assert not consumer.done(), "Turn abandoned an active filesystem worker"
            if closer is not None:
                assert not closer.done(), "Runtime closed before its patch worker exited"
            assert not (tmp_path / "created.txt").exists()
            release.set()
            await asyncio.wait_for(consumer, 5)
            if closer is not None:
                await asyncio.wait_for(closer, 5)
            if worker_fails:
                assert not (tmp_path / "created.txt").exists()
            else:
                assert (tmp_path / "created.txt").read_text() == "written\n"
            assert sum(isinstance(event, TurnCancelled) for event in events) == 1
        finally:
            release.set()
            await runtime.aclose()
            await asyncio.gather(consumer, return_exceptions=True)
            if closer is not None:
                await asyncio.gather(closer, return_exceptions=True)

    asyncio.run(scenario())
