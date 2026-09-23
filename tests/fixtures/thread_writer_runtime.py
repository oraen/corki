"""Owned subprocess fixture: the parent kills this Runtime while its writer is live."""

import asyncio
import sys
from pathlib import Path

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.ids import ThreadId, new_tool_call_id
from corki.protocol.items import ToolCallItem, new_step_id
from corki.protocol.tools import ToolCall, ToolSpec
from corki.tools import ToolRegistry


async def main():
    database, thread, mode = Path(sys.argv[1]), ThreadId(sys.argv[2]), sys.argv[3]

    class Tool:
        spec = ToolSpec("hold", "Fixture side effect", {"type": "object"})

        async def execute(self, call, context):
            (database.parent / "effect.txt").write_text("performed once")
            print("READY", flush=True)
            await asyncio.Event().wait()

    class Model:
        async def stream(self, request):
            call = ToolCall(new_tool_call_id(), "hold", {})
            yield ModelCompleted((ToolCallItem(call, request.items[-1].turn_id, new_step_id()),))

        async def aclose(self):
            pass

    registry = ToolRegistry()
    registry.register(Tool())
    runtime = await LangGraphRuntime.acreate(
        settings=CorkiSettings(working_directory=database.parent, skills_enabled=False),
        database_path=database,
        thread_id=thread,
        home_path=database.parent / "home",
        registry=registry,
        model=Model(),
    )
    try:
        if mode == "tool":
            async for _ in runtime.stream("perform operation"):
                pass
        else:
            await runtime._ensure_ready()
            print("READY", flush=True)
            await asyncio.Event().wait()
    finally:
        await runtime.aclose()


asyncio.run(main())
