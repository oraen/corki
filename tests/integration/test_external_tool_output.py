import asyncio
import json
import sqlite3

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.mcp.tools import MCPTool
from corki.models import ModelCompleted
from corki.protocol.events import TurnCancelled, TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize("flag,enabled", [(True, True), (False, True), (True, False)])
@pytest.mark.parametrize("is_error", [False, True])
@pytest.mark.parametrize("code_mode", [False, True])
def test_external_result_fact_reaches_memory_policy_and_ledger(
    tmp_path, flag, enabled, is_error, code_mode
):
    async def scenario():
        class External:
            spec = ToolSpec("fetch_record", "External data fixture", {"type": "object"})
            calls = 0

            async def execute(self, call, context):
                self.calls += 1
                return ToolResult(
                    call.id,
                    call.name,
                    "external data",
                    is_error=is_error,
                    contains_external_context=flag,
                )

        class Model:
            count = 0

            async def stream(self, request):
                self.count += 1
                turn, step = request.items[-1].turn_id, new_step_id()
                if self.count == 1:
                    call = (
                        ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            "text(await tools.fetch_record({}));",
                            input_kind="freeform",
                        )
                        if code_mode
                        else ToolCall(new_tool_call_id(), "fetch_record", {})
                    )
                    yield ModelCompleted((ToolCallItem(call, turn, step),))
                else:
                    result = next(
                        i for i in reversed(request.items) if isinstance(i, ToolResultItem)
                    )
                    assert "external data" in result.content
                    if not code_mode:
                        assert result.is_error == is_error
                        assert result.contains_external_context == flag
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        external, registry = External(), ToolRegistry()
        registry.register(external)
        database = tmp_path / "sessions.db"
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                memories_generate=False,
                memories_background_enabled=False,
                memories_disable_on_external_context=enabled,
                tool_mode="code_mode" if code_mode else "direct",
            ),
            database_path=database,
            registry=registry,
            model=Model(),
            memory_root=tmp_path / "memories",
        )
        try:
            # Keep an eligible source while suppressing this fixture's background worker.
            await runtime.set_thread_memory_mode("enabled")
            assert isinstance([e async for e in runtime.stream("fetch data")][-1], TurnCompleted)
            assert external.calls == 1
            with sqlite3.connect(database) as connection:
                assert connection.execute("SELECT memory_mode FROM threads").fetchone()[0] == (
                    "polluted" if flag and enabled else "enabled"
                )
                payloads = [
                    json.loads(r[0])
                    for r in connection.execute(
                        "SELECT result_json FROM tool_executions WHERE result_json IS NOT NULL"
                    )
                ]
                saved = next(r for r in payloads if r["tool_name"] == "fetch_record")
                assert saved.get("contains_external_context", False) == flag
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_empty_tool_search_result_marks_thread(tmp_path):
    from corki.tools.search import ToolSearchTool

    async def scenario():
        registry = ToolRegistry()
        registry.register(ToolSearchTool(registry))

        class Model:
            count = 0

            async def stream(self, request):
                self.count += 1
                turn, step = request.items[-1].turn_id, new_step_id()
                if self.count == 1:
                    item = ToolCallItem(
                        ToolCall(new_tool_call_id(), "tool_search", {"query": "nothing"}),
                        turn,
                        step,
                    )
                else:
                    result = next(
                        i for i in reversed(request.items) if isinstance(i, ToolResultItem)
                    )
                    assert not result.is_error and result.contains_external_context
                    assert json.loads(result.content)["tools"] == []
                    item = AssistantMessageItem("done", turn, step)
                yield ModelCompleted((item,))

            async def aclose(self):
                pass

        database = tmp_path / "sessions.db"
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                memories_generate=False,
                memories_background_enabled=False,
                memories_disable_on_external_context=True,
            ),
            database_path=database,
            registry=registry,
            model=Model(),
            memory_root=tmp_path / "memories",
        )
        try:
            assert isinstance([e async for e in runtime.stream("find a tool")][-1], TurnCompleted)
            with sqlite3.connect(database) as connection:
                assert (
                    connection.execute("SELECT memory_mode FROM threads").fetchone()[0]
                    == "polluted"
                )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_cold_resume_marks_cached_external_result_without_reexecuting(tmp_path):
    from corki.protocol.ids import new_thread_id, new_turn_id
    from corki.protocol.items import UserMessageItem
    from corki.sessions import TurnRecord, TurnStatus
    from corki.storage import SQLiteSessionRepository

    async def scenario():
        database = tmp_path / "sessions.db"
        repository = SQLiteSessionRepository(database)
        thread, turn = new_thread_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        await repository.save_turn(TurnRecord(turn, thread, TurnStatus.RUNNING, "fetch"))
        await repository.append_items(thread, (UserMessageItem("fetch", turn),))
        call = ToolCall(new_tool_call_id(), "fetch_record", {})
        await repository.commit_model_step(
            thread, turn, 0, ModelCompleted((ToolCallItem(call, turn, new_step_id()),))
        )
        await repository.claim_tool_call(thread, turn, call)
        await repository.complete_tool_call(
            thread,
            turn,
            ToolResult(call.id, call.name, "cached data", contains_external_context=True),
        )

        class Tool:
            spec = ToolSpec("fetch_record", "Fixture", {"type": "object"})
            calls = 0

            async def execute(self, call, context):
                self.calls += 1
                raise AssertionError("committed external operation must not replay")

        class Model:
            async def stream(self, request):
                result = next(i for i in reversed(request.items) if isinstance(i, ToolResultItem))
                assert result.content == "cached data" and result.contains_external_context
                yield ModelCompleted((AssistantMessageItem("done", turn, new_step_id()),))

            async def aclose(self):
                pass

        registry, tool = ToolRegistry(), Tool()
        registry.register(tool)
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                memories_generate=False,
                memories_background_enabled=False,
                memories_disable_on_external_context=True,
            ),
            database_path=database,
            thread_id=thread,
            registry=registry,
            model=Model(),
            memory_root=tmp_path / "memories",
        )
        try:
            assert isinstance([e async for e in runtime.resume_pending()][-1], TurnCompleted)
            assert tool.calls == 0
            with sqlite3.connect(database) as connection:
                assert (
                    connection.execute("SELECT memory_mode FROM threads").fetchone()[0]
                    == "polluted"
                )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("name", ["mcp__fixture::read", "fetch_record"])
@pytest.mark.parametrize("cancel", [False, True])
def test_pollution_write_failure_warns_without_aborting_tool_or_turn(
    tmp_path, caplog, name, cancel
):
    async def scenario():
        class Tool:
            spec = ToolSpec(name, "Fixture", {"type": "object"})
            calls = 0

            async def execute(self, call, context):
                self.calls += 1
                return ToolResult(
                    call.id, call.name, "done", contains_external_context=name == "fetch_record"
                )

        class Model:
            count = 0

            async def stream(self, request):
                self.count += 1
                turn, step = request.items[-1].turn_id, new_step_id()
                item = (
                    ToolCallItem(ToolCall(new_tool_call_id(), name, {}), turn, step)
                    if self.count == 1
                    else AssistantMessageItem("done", turn, step)
                )
                yield ModelCompleted((item,))

            async def aclose(self):
                pass

        tool, registry = Tool(), ToolRegistry()
        if name == "mcp__fixture::read":

            class Client:
                async def call_tool(self, remote_name, arguments):
                    tool.calls += 1
                    return {"content": [{"type": "text", "text": "done"}]}

            registry.register(MCPTool("fixture", {"name": "read"}, Client()))
        else:
            registry.register(tool)
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                memories_generate=False,
                memories_background_enabled=False,
                memories_disable_on_external_context=True,
            ),
            database_path=tmp_path / "sessions.db",
            registry=registry,
            model=Model(),
            memory_root=tmp_path / "memories",
        )

        async def fail(*args):
            if cancel:
                raise asyncio.CancelledError
            raise OSError("database unavailable")

        runtime._memory_repository.mark_thread_mode = fail
        try:
            events = []

            async def collect():
                async for event in runtime.stream("read"):
                    events.append(event)

            if cancel:
                with pytest.raises(asyncio.CancelledError):
                    await collect()
            else:
                await collect()
            assert isinstance(events[-1], TurnCancelled if cancel else TurnCompleted)
            assert tool.calls == (0 if cancel and name.startswith("mcp__") else 1)
            if not cancel:
                assert "pollution" in caplog.text.lower()
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
