"""Exit after a durable output prefix, while another side effect is unresolved."""

import asyncio
import os
import sys
from pathlib import Path

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelItemCompleted
from corki.protocol.items import ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall, ToolConcurrency, ToolResult, ToolSpec
from corki.storage import SQLiteSessionRepository
from corki.tools import ToolRegistry


async def main(directory, streamed):
    tail_started = asyncio.Event()

    class Repository(SQLiteSessionRepository):
        async def append_items(self, thread_id, items):
            await super().append_items(thread_id, items)
            if any(isinstance(i, ToolResultItem) and i.call_id == "head" for i in items):
                assert tail_started.is_set()
                os._exit(29)

    class Tool:
        spec = ToolSpec("probe", "fixture", {}, concurrency=ToolConcurrency.PARALLEL)

        async def execute(self, call, context):
            with (directory / "effects.txt").open("a") as output:
                output.write(str(call.id) + "\n")
                output.flush()
                os.fsync(output.fileno())
            if call.id == "tail":
                tail_started.set()
                await asyncio.Event().wait()
            await tail_started.wait()
            return ToolResult(call.id, call.name, "saved head")

    class Model:
        async def stream(self, request):
            step = new_step_id()
            calls = tuple(
                ToolCallItem(ToolCall(key, "probe", {}), request.items[-1].turn_id, step)
                for key in ("head", "tail")
            )
            if streamed:
                for call in calls:
                    yield ModelItemCompleted(call)
            yield ModelCompleted(calls)

        async def aclose(self):
            pass

    repository = Repository(directory / "sessions.db")
    registry = ToolRegistry()
    registry.register(Tool())
    runtime = await LangGraphRuntime.acreate(
        settings=CorkiSettings(working_directory=directory, skills_enabled=False),
        database_path=repository.path,
        repository=repository,
        registry=registry,
        model=Model(),
    )
    try:
        async for _ in runtime.stream("run"):
            pass
    finally:
        await runtime.aclose()


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2] == "streamed"))
