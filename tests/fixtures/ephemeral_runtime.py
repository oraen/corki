"""An owned child process killed before cleanup, proving no canonical disk state."""

import asyncio
import sys
from pathlib import Path

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import ToolCallItem, new_step_id
from corki.protocol.tools import ToolCall, ToolSpec
from corki.tools import ToolRegistry


async def main():
    root, phase = Path(sys.argv[1]), sys.argv[2]

    class Tool:
        spec = ToolSpec("effect", "Explicit fixture side effect", {"type": "object"})

        async def execute(self, call, context):
            (root / "effect.txt").write_text("explicit tool output")
            print("READY", flush=True)
            await asyncio.Event().wait()

    class Model:
        async def stream(self, request):
            if phase == "model":
                print("READY", flush=True)
                await asyncio.Event().wait()
            call = ToolCall(new_tool_call_id(), "effect", {})
            yield ModelCompleted((ToolCallItem(call, request.items[-1].turn_id, new_step_id()),))

        async def aclose(self):
            pass

    registry = ToolRegistry()
    registry.register(Tool())
    runtime = LangGraphRuntime.create(
        settings=CorkiSettings(working_directory=root, skills_enabled=False),
        database_path=root / "state" / "history.db",
        home_path=root / "home",
        model=Model(),
        registry=registry,
        ephemeral=True,
    )
    try:
        async for _ in runtime.stream("ephemeral fixture request"):
            pass
    finally:
        await runtime.aclose()


asyncio.run(main())
