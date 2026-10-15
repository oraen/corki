"""Reset must not race a tool already admitted by the shared memory worker."""

import asyncio
import json

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.memory.reset import MemoryResetError
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools.builtin.patch import ApplyPatchTool


@pytest.mark.parametrize("other_host", [False, True])
def test_reset_rejects_inflight_shared_worker_and_can_retry_after_close(
    tmp_path, monkeypatch, other_host
):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        execute = ApplyPatchTool.execute

        async def gated(tool, call, context):
            entered.set()
            await release.wait()
            return await execute(tool, call, context)

        monkeypatch.setattr(ApplyPatchTool, "execute", gated)

        class Main:
            async def stream(self, request):
                yield ModelCompleted(
                    (
                        AssistantMessageItem(
                            "ready",
                            request.items[-1].turn_id,
                            new_step_id(),
                        ),
                    )
                )

            async def aclose(self):
                pass

        class Worker(Main):
            calls = 0

            async def stream(self, request):
                self.calls += 1
                turn, step = request.items[-1].turn_id, new_step_id()
                if self.calls == 1:
                    item = ToolCallItem(
                        ToolCall(
                            new_tool_call_id(),
                            "apply_patch",
                            {
                                "patch": "*** Begin Patch\n*** Add File: inflight.md\n"
                                "+owned\n*** End Patch",
                            },
                        ),
                        turn,
                        step,
                    )
                else:
                    item = AssistantMessageItem(
                        json.dumps(
                            {
                                "memory": "owned fact",
                                "memory_summary": "owned index",
                                "skills": [],
                            }
                        ),
                        turn,
                        step,
                    )
                yield ModelCompleted((item,))

        root = tmp_path / "memories"

        async def create():
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(tmp_path, skills_enabled=False, memories_enabled=True),
                database_path=tmp_path / "history.db",
                home_path=tmp_path / "home",
                memory_root=root,
                model=Main(),
                memory_model=Worker(),
            )

        runtime = await create()
        host = await create() if other_host else runtime
        try:
            assert isinstance([e async for e in runtime.stream("work")][-1], TurnCompleted)
            await asyncio.wait_for(entered.wait(), 3)
            with pytest.raises(MemoryResetError, match="busy") as failed:
                await host.reset_memory()
            assert not failed.value.database_cleared
            release.set()
            report = await asyncio.wait_for(runtime._memory_service.wait(), 3)
            assert report.consolidated and not report.failed
            assert (root / "inflight.md").read_text().strip() == "owned"
            await host.reset_memory()
            assert list(root.iterdir()) == []
        finally:
            release.set()
            await runtime.aclose()
            if host is not runtime:
                await host.aclose()

    asyncio.run(scenario())
