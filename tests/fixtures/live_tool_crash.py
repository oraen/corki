"""Exit the real Runtime at a selected durable partial-response boundary."""

import asyncio
import os
import sys
from pathlib import Path

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelItemCompleted
from corki.protocol.ids import ToolCallId
from corki.protocol.items import ToolCallItem, new_step_id
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.storage import SQLiteSessionRepository
from corki.tools import ToolRegistry


async def main(directory: Path, phase: str, input_kind: str = "json") -> None:
    class Repository(SQLiteSessionRepository):
        async def append_partial_item(self, *args):
            await super().append_partial_item(*args)
            if phase == "unclaimed":
                os._exit(23)

        async def complete_tool_call(self, *args):
            await super().complete_tool_call(*args)
            if phase == "completed":
                os._exit(23)

    class Tool:
        spec = ToolSpec("probe", "crash fixture", {"type": "object"}, input_kind=input_kind)

        async def execute(self, call, context):
            with (directory / "side-effect.txt").open("a") as output:
                output.write("executed\n")
                output.flush()
                os.fsync(output.fileno())
            if phase == "running":
                os._exit(23)
            return ToolResult(call.id, call.name, "saved")

    class Model:
        async def stream(self, request):
            yield ModelItemCompleted(
                ToolCallItem(
                    ToolCall(
                        ToolCallId("crash-call"),
                        "probe",
                        None if input_kind == "freeform" else {},
                        raw_arguments="text('原样');\n" if input_kind == "freeform" else "",
                        input_kind=input_kind,
                    ),
                    request.items[-1].turn_id,
                    new_step_id(),
                )
            )
            await asyncio.Event().wait()

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
        async for _ in runtime.stream("run crash fixture"):
            pass
    finally:
        await runtime.aclose()


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "json"))
