"""Abrupt parent death must not leave a busy JS engine process alive."""

import asyncio
import os
import sys
from pathlib import Path

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import ToolOutputDelta
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import ToolCallItem, new_step_id
from corki.protocol.tools import ToolCall


async def main(directory):
    requests = []

    class Model:
        async def stream(self, request):
            requests.append(request)
            if len(requests) == 1:
                call = ToolCall(
                    new_tool_call_id(),
                    "exec",
                    None,
                    input_kind="freeform",
                    raw_arguments='// @exec: {"yield_time_ms":0}\nnotify("started"); while(true){}',
                )
                yield ModelCompleted(
                    (ToolCallItem(call, request.items[-1].turn_id, new_step_id()),)
                )
            else:
                await asyncio.Event().wait()

        async def aclose(self):
            pass

    runtime = LangGraphRuntime.create(
        settings=CorkiSettings(
            working_directory=directory, skills_enabled=False, tool_mode="code_mode_only"
        ),
        database_path=directory / "sessions.db",
        model=Model(),
    )
    async for event in runtime.stream("busy cell"):
        if isinstance(event, ToolOutputDelta) and event.delta == "started":
            print(next(iter(runtime._code_mode.cells.values())).process.pid, flush=True)
            os._exit(23)


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1])))
