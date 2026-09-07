import asyncio
import json
import sqlite3
from dataclasses import replace

import httpx
import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import OpenAIResponsesModel, resolve_capabilities
from corki.protocol.events import TurnCompleted, TurnFailed
from corki.tools import ToolRegistry


def external_payload(kind):
    if kind == "native_search":
        return {
            "type": "tool_search_call",
            "id": "search-1",
            "call_id": "call-1",
            "execution": "client",
            "arguments": None,
        }
    if kind == "ordinary_search":
        return {
            "type": "function_call",
            "id": "search-1",
            "call_id": "call-1",
            "name": "tool_search",
            "arguments": "null",
        }
    if kind == "web":
        return {
            "type": "web_search_call",
            "id": "web-1",
            "status": "completed",
            "action": {"type": "search", "query": "external fixture"},
        }
    if kind == "notification":
        return {
            "type": "function_call_output",
            "id": "notify-1",
            "name": "notifications",
            "namespace": "docs",
            "output": "external fixture",
        }
    if kind == "search_output":
        return {
            "type": "tool_search_output",
            "id": "output-1",
            "call_id": None,
            "execution": "server",
            "status": "completed",
            "tools": [],
        }
    return {
        "type": "tool_search_call",
        "id": "server-1",
        "call_id": None,
        "execution": "server",
        "arguments": {"query": "external fixture"},
    }


@pytest.mark.parametrize(
    "kind",
    ["native_search", "ordinary_search", "web", "notification", "search_output", "server_search"],
)
@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
def test_completed_external_item_is_durable_before_response_terminal(
    tmp_path, kind, failed, enabled
):
    async def scenario():
        database = tmp_path / "sessions.db"
        requests = []
        source = external_payload(kind)

        def respond(request):
            requests.append(json.loads(request.content))
            if len(requests) == 1:
                events = [{"type": "response.output_item.done", "output_index": 0, "item": source}]
                if failed:
                    events.append(
                        {
                            "type": "response.failed",
                            "response": {
                                "error": {
                                    "code": "invalid_prompt",
                                    "message": "fixture failure",
                                }
                            },
                        }
                    )
                else:
                    events.append(
                        {
                            "type": "response.completed",
                            "response": {"id": "r-1", "output": [source]},
                        }
                    )
            else:
                events = [{"type": "response.completed", "response": {"id": "r-2", "output": []}}]
            return httpx.Response(
                200, text="".join("data: " + json.dumps(e) + "\n\n" for e in events)
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        model = OpenAIResponsesModel(
            api_key="fixture",
            base_url="https://api.openai.com/v1",
            client=client,
            capabilities=replace(
                resolve_capabilities(base_url="https://api.openai.com/v1", api_mode="responses"),
                supports_native_tool_search=True,
            ),
        )
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                api_mode="responses",
                tool_search_mode="native",
                memories_enabled=True,
                memories_generate=False,
                memories_disable_on_external_context=enabled,
            ),
            database_path=database,
            registry=ToolRegistry(),
            model=model,
            memory_root=tmp_path / "memories",
        )
        try:
            events = [e async for e in runtime.stream("test external event")]
            assert isinstance(events[-1], TurnFailed if failed else TurnCompleted), events[-1]
            with sqlite3.connect(database) as db:
                assert db.execute("SELECT memory_mode FROM threads").fetchone()[0] == (
                    "enabled" if kind == "ordinary_search" or not enabled else "polluted"
                )
            stored = await runtime._repository.load_items(runtime.thread_id)
            if kind in {"native_search", "ordinary_search"}:
                calls = [i for i in stored if hasattr(i, "call")]
                assert len(calls) == 1
                assert calls[0].contains_external_context == (kind == "native_search")
            else:
                hosted = [i for i in stored if type(i).__name__ == "HostedToolItem"]
                assert len(hosted) == 1 and json.loads(hosted[0].payload_json) == source
            if not failed:
                assert isinstance([e async for e in runtime.stream("follow up")][-1], TurnCompleted)
                if kind not in {"native_search", "ordinary_search"}:
                    assert source in requests[-1]["input"]
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["web", "notification", "search_output", "server_search"])
def test_cold_hosted_history_uses_chat_compatibility_and_notification_startup_policy(
    tmp_path, kind
):
    from corki.models import OpenAICompatibleModel
    from corki.protocol.ids import new_thread_id, new_turn_id
    from corki.protocol.items import HostedToolItem, new_step_id
    from corki.storage import SQLiteSessionRepository

    async def scenario():
        database = tmp_path / "sessions.db"
        repository = SQLiteSessionRepository(database)
        thread = new_thread_id()
        await repository.create_thread(thread, tmp_path)
        item = HostedToolItem(json.dumps(external_payload(kind)), new_turn_id(), new_step_id())
        await repository.append_items(thread, (item,))
        messages = await repository.load_messages(thread)
        assert messages[0].content == item.compatibility_content
        requests = []

        def respond(request):
            body = json.loads(request.content)
            requests.append(body)
            found = [m for m in body["messages"] if m.get("content") == item.compatibility_content]
            assert len(found) == 1 and found[0]["role"] == "user"
            return httpx.Response(
                200,
                text=(
                    'data: {"choices":[{"index":0,"delta":{"content":"done"},'
                    '"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
                ),
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                memories_generate=False,
                memories_disable_on_external_context=True,
            ),
            database_path=database,
            registry=ToolRegistry(),
            thread_id=thread,
            model=OpenAICompatibleModel(
                api_key="fixture",
                base_url="https://fixture.test/v1",
                client=client,
                capabilities=resolve_capabilities(
                    base_url="https://fixture.test/v1", api_mode="chat_completions"
                ),
            ),
            memory_root=tmp_path / "memories",
        )
        try:
            assert isinstance([e async for e in runtime.stream("follow up")][-1], TurnCompleted)
            assert len(requests) == 1
            with sqlite3.connect(database) as db:
                assert db.execute("SELECT memory_mode FROM threads").fetchone()[0] == (
                    "polluted" if kind == "notification" else "enabled"
                )
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel", [False, True])
def test_hosted_mark_failure_isolated_but_cancellation_preserves_durable_item(
    tmp_path, caplog, cancel
):
    from corki.models import ModelCompleted, ModelItemCompleted
    from corki.protocol.events import TurnCancelled
    from corki.protocol.items import HostedToolItem, new_step_id

    async def scenario():
        class Model:
            async def stream(self, request):
                item = HostedToolItem(
                    json.dumps(external_payload("web")), request.items[-1].turn_id, new_step_id()
                )
                yield ModelItemCompleted(item)
                yield ModelCompleted((item,))

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                memories_generate=False,
                memories_disable_on_external_context=True,
            ),
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
            model=Model(),
            memory_root=tmp_path / "memories",
        )

        async def fail(*args):
            if cancel:
                raise asyncio.CancelledError
            raise OSError("memory unavailable")

        runtime._memory_repository.mark_thread_mode = fail
        events = []
        try:

            async def collect():
                async for event in runtime.stream("external"):
                    events.append(event)

            if cancel:
                with pytest.raises(asyncio.CancelledError):
                    await collect()
            else:
                await collect()
            assert isinstance(events[-1], TurnCancelled if cancel else TurnCompleted)
            stored = await runtime._repository.load_items(runtime.thread_id)
            assert len([i for i in stored if isinstance(i, HostedToolItem)]) == 1
            if not cancel:
                assert "pollution" in caplog.text
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_hosted_event_survives_compaction_and_actual_history_search_read(tmp_path):
    from corki.config import TokenBudgetConfig
    from corki.models import ModelCompleted
    from corki.protocol.ids import new_tool_call_id
    from corki.protocol.items import (
        AssistantMessageItem,
        HostedToolItem,
        ToolCallItem,
        ToolResultItem,
        new_step_id,
    )
    from corki.protocol.tools import ToolCall

    async def scenario():
        class Model:
            count = 0
            original = None

            async def stream(self, request):
                self.count += 1
                turn, step = request.items[-1].turn_id, new_step_id()
                if self.count == 1:
                    self.original = HostedToolItem(
                        json.dumps(external_payload("notification")), turn, step
                    )
                    item = self.original
                elif self.count == 2:
                    assert self.original in request.items and request.tools == ()
                    item = AssistantMessageItem("Archive available; details omitted", turn, step)
                elif self.count == 3:
                    assert self.original not in request.items
                    item = ToolCallItem(
                        ToolCall(
                            new_tool_call_id(),
                            "history::search_contents",
                            {"query": "external fixture", "role": "tool"},
                        ),
                        turn,
                        step,
                    )
                elif self.count == 4:
                    result = next(
                        i for i in reversed(request.items) if isinstance(i, ToolResultItem)
                    )
                    found = json.loads(result.content)["items"][0]
                    assert found["item_id"] == str(self.original.id)
                    item = ToolCallItem(
                        ToolCall(
                            new_tool_call_id(),
                            "history::read_item",
                            {"item_id": found["item_id"], "window_id": found["window_id"]},
                        ),
                        turn,
                        step,
                    )
                else:
                    result = next(
                        i for i in reversed(request.items) if isinstance(i, ToolResultItem)
                    )
                    assert "external fixture" in json.loads(result.content)["text"]
                    item = AssistantMessageItem("recalled", turn, step)
                yield ModelCompleted((item,))

            async def aclose(self):
                pass

        model = Model()
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                memories_generate=False,
                memories_disable_on_external_context=True,
            ),
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
            model=model,
            memory_root=tmp_path / "memories",
        )
        try:
            assert isinstance([e async for e in runtime.stream("receive")][-1], TurnCompleted)
            assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
            thread = runtime.thread_id
            settings = replace(
                runtime._settings,
                token_budget_enabled=True,
                token_budget=TokenBudgetConfig(use_history_notes_extension=True),
            )
            await runtime.aclose()
            runtime = LangGraphRuntime.create(
                settings=settings,
                database_path=tmp_path / "sessions.db",
                thread_id=thread,
                registry=ToolRegistry(),
                model=model,
                memory_root=tmp_path / "memories",
            )
            assert isinstance(
                [e async for e in runtime.stream("recall the event")][-1], TurnCompleted
            )
            assert model.count == 5
            assert model.original in await runtime._repository.load_items(runtime.thread_id)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize(
    "kind",
    ["web", "notification", "search_output", "server_search", "native_search", "ordinary_search"],
)
def test_cold_recovery_reuses_hosted_fact_without_resampling_original_step(tmp_path, partial, kind):
    from corki.models import ModelCompleted
    from corki.protocol.ids import new_thread_id, new_tool_call_id, new_turn_id
    from corki.protocol.items import HostedToolItem, ToolCallItem, UserMessageItem, new_step_id
    from corki.protocol.tools import ToolCall
    from corki.sessions import TurnRecord, TurnStatus
    from corki.storage import SQLiteSessionRepository

    async def scenario():
        database = tmp_path / "sessions.db"
        repository = SQLiteSessionRepository(database)
        thread, turn = new_thread_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        await repository.save_turn(TurnRecord(turn, thread, TurnStatus.RUNNING, "external"))
        await repository.append_items(thread, (UserMessageItem("external", turn),))
        source = external_payload(kind)
        local_call = kind in {"native_search", "ordinary_search"}
        item = (
            ToolCallItem(
                ToolCall(new_tool_call_id(), "tool_search", None, "null", parse_error="not object"),
                turn,
                new_step_id(),
                contains_external_context=kind == "native_search",
            )
            if local_call
            else HostedToolItem(json.dumps(source), turn, new_step_id())
        )
        if partial:
            await repository.append_partial_item(thread, turn, 0, item)
        else:
            await repository.commit_model_step(thread, turn, 0, ModelCompleted((item,)))
        requests = []

        def respond(request):
            body = json.loads(request.content)
            requests.append(body)
            if not local_call:
                assert source in body["input"]
            return httpx.Response(
                200, text='data: {"type":"response.completed","response":{"id":"new-step"}}\n\n'
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        model = OpenAIResponsesModel(
            api_key="fixture",
            base_url="https://api.openai.com/v1",
            client=client,
            capabilities=replace(
                resolve_capabilities(base_url="https://api.openai.com/v1", api_mode="responses"),
                supports_native_tool_search=True,
            ),
        )
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                api_mode="responses",
                tool_search_mode="native",
                memories_enabled=True,
                memories_generate=False,
                memories_disable_on_external_context=True,
            ),
            database_path=database,
            registry=ToolRegistry(),
            model=model,
            thread_id=thread,
            memory_root=tmp_path / "memories",
        )
        try:
            assert isinstance([e async for e in runtime.resume_pending()][-1], TurnCompleted)
            assert len(requests) == int(partial or local_call)
            with sqlite3.connect(database) as db:
                assert db.execute("SELECT memory_mode FROM threads").fetchone()[0] == (
                    "enabled" if kind == "ordinary_search" else "polluted"
                )
            stored = await runtime._repository.load_items(thread)
            assert [i for i in stored if isinstance(i, (HostedToolItem, ToolCallItem))] == [item]
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())
