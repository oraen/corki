"""Crash after a real failed-attempt commit, before its graph checkpoint."""

import asyncio
import os
import sys
from pathlib import Path

import corki as corki_package
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelError, ModelErrorKind, ModelItemCompleted
from corki.protocol.ids import ToolCallId
from corki.protocol.items import ToolCallItem, new_step_id
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.storage import SQLiteSessionRepository
from corki.tools import ToolRegistry


async def main(directory, crash_retry, kind=ModelErrorKind.TRANSPORT):
    expected_package = os.environ.get("CORKI_EXPECTED_PACKAGE")
    if expected_package is not None:
        assert (
            Path(corki_package.__file__).resolve().parent.parent == Path(expected_package).resolve()
        )
    if kind == ModelErrorKind.CONNECTION:
        import corki.core.graph

        async def fast_wait(delay, realtime):
            await asyncio.sleep(0)

        corki.core.graph.wait_retry = fast_wait

    class Repository(SQLiteSessionRepository):
        async def save_model_failure(self, thread, turn, index, failure):
            await super().save_model_failure(thread, turn, index, failure)
            used = (
                failure.connection_retries_used
                if kind == ModelErrorKind.CONNECTION
                else failure.retries_used
            )
            if used == crash_retry:
                os._exit(23)

    class Tool:
        spec = ToolSpec("probe", "fixture", {"type": "object"})

        async def execute(self, call, context):
            with (directory / "side-effect.txt").open("a") as output:
                output.write("executed\n")
                output.flush()
                os.fsync(output.fileno())
            return ToolResult(call.id, call.name, "observed")

    class Model:
        calls = 0

        async def stream(self, request):
            self.calls += 1
            if self.calls == 1 and kind != ModelErrorKind.CONNECTION:
                yield ModelItemCompleted(
                    ToolCallItem(
                        ToolCall(ToolCallId("c"), "probe", {}),
                        request.items[-1].turn_id,
                        new_step_id(),
                    )
                )
            raise ModelError("fixture interrupted", kind=kind, retryable=True)

        async def aclose(self):
            pass

    repository = Repository(directory / "sessions.db")
    registry = ToolRegistry()
    registry.register(Tool())
    runtime = await LangGraphRuntime.acreate(
        settings=CorkiSettings(
            working_directory=directory,
            skills_enabled=False,
            model_max_retries=1,
            model_retry_base_seconds=0.001,
        ),
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
    kind = ModelErrorKind(sys.argv[3]) if len(sys.argv) > 3 else ModelErrorKind.TRANSPORT
    asyncio.run(main(Path(sys.argv[1]), int(sys.argv[2]), kind))
