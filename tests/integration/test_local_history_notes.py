import asyncio
import json

import httpx
import pytest

from corki.config import CorkiSettings, TokenBudgetConfig
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


class SequenceModel:
    def __init__(self, actions=()):
        self.actions, self.requests = list(actions), []

    async def stream(self, request):
        self.requests.append(request)
        turn = request.items[-1].turn_id
        if self.actions:
            action = self.actions.pop(0)
            name, arguments = action(request) if callable(action) else action
            item = ToolCallItem(ToolCall(new_tool_call_id(), name, arguments), turn, new_step_id())
        else:
            item = AssistantMessageItem("done", turn, new_step_id())
        yield ModelCompleted((item,))

    async def aclose(self):
        pass


@pytest.mark.parametrize("tool_mode", ["direct", "code_mode_only"])
def test_all_nine_local_actions_execute_through_the_runtime(tmp_path, tool_mode):
    async def scenario():
        def read_found(request):
            result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
            found = json.loads(result.content)["items"][0]
            return "history::read_item", {
                "item_id": found["item_id"],
                "window_id": found["window_id"],
            }

        model = SequenceModel(
            [
                ("history::list_windows", {}),
                ("history::list_items", {"role": "user", "limit": 1, "recent_first": True}),
                read_found,
                ("history::search_contents", {"query": "MAIN_TASK", "role": "user"}),
                ("notes::write_file", {"path": "p", "text": "first\n"}),
                ("notes::append_to_file", {"path": "p", "text": "second\n"}),
                ("notes::list_files_by_prefix", {"prefix": "/root/notes"}),
                ("notes::search_contents", {"query": "second", "max_matches_per_file": 1}),
                ("notes::read_file", {"path": "p", "start_line": -1}),
            ]
        )
        runtime = LangGraphRuntime.create(
            settings=settings(tmp_path, tool_mode=tool_mode),
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
            model=model,
        )
        try:
            assert isinstance([e async for e in runtime.stream("MAIN_TASK")][-1], TurnCompleted)
            results = [i for i in model.requests[-1].items if isinstance(i, ToolResultItem)]
            assert len(results) == 9 and all(not r.is_error for r in results)
            assert (
                json.loads(results[0].content)["windows"][0]["window_id"]
                == f"{runtime.thread_id}:0"
            )
            assert json.loads(results[2].content)["text"] == "MAIN_TASK"
            assert json.loads(results[6].content)["files"][0]["path"] == "/root/notes/p"
            assert json.loads(results[7].content)["matches"][0]["line"] == 2
            assert json.loads(results[8].content)["text"] == "second\n"
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "mode,tool_mode,namespace",
    [
        ("chat_completions", "direct", "compatible"),
        ("chat_completions", "code_mode_only", "compatible"),
        ("responses", "direct", "compatible"),
        ("responses", "direct", "native"),
    ],
)
def test_local_recovery_real_provider_payload_and_no_native_http(
    tmp_path, monkeypatch, mode, tool_mode, namespace
):
    from corki.protocol.tool_names import compatible_tool_name

    async def scenario():
        payloads, clients = [], []

        def respond(request):
            assert request.url.path.endswith(
                "/responses" if mode == "responses" else "/chat/completions"
            )
            body = json.loads(request.content)
            payloads.append(body)
            assert "client_metadata" not in body and "x-codex-turn-metadata" not in request.headers
            assert '"encrypted": true' not in json.dumps(body["tools"])
            index = len(payloads)
            name, args = (
                ("notes::write_file", {"path": "p", "text": "LOCAL_SAVED_NOTE"})
                if index == 1
                else ("new_context", {})
                if index == 2
                else ("notes::read_file", {"path": "p"})
            )
            calling = index in (1, 2, 3, 5)
            wire_name = compatible_tool_name(name)
            if mode == "responses":
                ns, _, leaf = name.rpartition("::")
                output = (
                    [
                        {
                            "type": "function_call",
                            "id": f"i-{index}",
                            "call_id": f"c-{index}",
                            "name": leaf if namespace == "native" else wire_name,
                            **({"namespace": ns} if ns and namespace == "native" else {}),
                            "arguments": json.dumps(args),
                        }
                    ]
                    if calling
                    else [
                        {
                            "type": "message",
                            "id": f"m-{index}",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "done"}],
                        }
                    ]
                )
                packet = {
                    "type": "response.completed",
                    "response": {"id": f"r-{index}", "output": output},
                }
            else:
                delta = (
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": f"c-{index}",
                                "type": "function",
                                "function": {"name": wire_name, "arguments": json.dumps(args)},
                            }
                        ]
                    }
                    if calling
                    else {"content": "done"}
                )
                packet = {
                    "choices": [
                        {
                            "index": 0,
                            "delta": delta,
                            "finish_reason": "tool_calls" if calling else "stop",
                        }
                    ]
                }
            return httpx.Response(200, text=f"data: {json.dumps(packet)}\n\n")

        actual_client = httpx.AsyncClient

        def create_client(*args, **kwargs):
            client = actual_client(*args, **kwargs, transport=httpx.MockTransport(respond))
            clients.append(client)
            return client

        monkeypatch.setattr(httpx, "AsyncClient", create_client)

        def create(thread=None):
            return LangGraphRuntime.create(
                settings=settings(
                    tmp_path,
                    api_mode=mode,
                    api_key="ordinary-fixture",
                    provider_name="openai",
                    tool_mode=tool_mode,
                    tool_namespace_mode=namespace,
                ),
                database_path=tmp_path / "sessions.db",
                registry=ToolRegistry(),
                thread_id=thread,
            )

        runtime = create()
        thread = runtime.thread_id
        try:
            assert isinstance([e async for e in runtime.stream("ORIGINAL_TASK")][-1], TurnCompleted)
            assert "ORIGINAL_TASK" not in json.dumps(payloads[2])
            assert "LOCAL_SAVED_NOTE" not in json.dumps(payloads[2])
            assert "LOCAL_SAVED_NOTE" in json.dumps(payloads[3])
        finally:
            await runtime.aclose()
        cold = create(thread)
        try:
            assert isinstance([e async for e in cold.stream("continue")][-1], TurnCompleted)
            assert "LOCAL_SAVED_NOTE" in json.dumps(payloads[5])
            assert len(payloads) == 6
        finally:
            await cold.aclose()
        assert all(client.is_closed for client in clients)
        assert len(clients) == 2  # Inference only; local recovery has no HTTP client.

    asyncio.run(scenario())


def settings(tmp_path, **overrides):
    return CorkiSettings(
        working_directory=tmp_path,
        skills_enabled=False,
        token_budget_enabled=True,
        token_budget=TokenBudgetConfig(use_history_notes_extension=True),
        **overrides,
    )


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
def test_malformed_provider_call_is_recalled_after_reset_and_cold_start(
    tmp_path, monkeypatch, mode
):
    from corki.protocol.tool_names import compatible_tool_name
    from corki.protocol.tools import ToolSpec

    async def scenario():
        payloads, recovered_ids, clients = [], [], []
        raw = '{ "RECOVERY_RAW_界": '

        class Guarded:
            spec = ToolSpec("guarded", "Must not execute malformed input", {"type": "object"})
            calls = 0

            async def execute(self, call, context):
                self.calls += 1
                raise AssertionError("malformed input reached the handler")

        guarded = Guarded()

        def respond(request):
            body = json.loads(request.content)
            payloads.append(body)
            index = len(payloads)
            items = body["input"] if mode == "responses" else body["messages"]
            outputs = [
                item.get("output", item.get("content"))
                for item in items
                if item.get("type") == "function_call_output" or item.get("role") == "tool"
            ]
            # Responses may encode text as typed input_text content.
            outputs = [value if isinstance(value, str) else value[0]["text"] for value in outputs]
            if index == 1:
                name, arguments = "guarded", raw
            elif index in (2, 6):
                if index == 2:
                    assert "invalid JSON arguments" in outputs[-1]
                    calls = [
                        item
                        for item in items
                        if item.get("type") == "function_call" or item.get("tool_calls")
                    ]
                    replay = (
                        calls[-1]["arguments"]
                        if mode == "responses"
                        else calls[-1]["tool_calls"][0]["function"]["arguments"]
                    )
                    assert replay == raw
                name, arguments = "new_context", "{}"
            elif index in (3, 7):
                assert "RECOVERY_RAW_" not in json.dumps(items)
                name, arguments = (
                    "history::search_contents",
                    json.dumps({"query": raw, "role": "assistant", "tool_name": "guarded"}),
                )
            elif index in (4, 8):
                found = json.loads(outputs[-1])["items"]
                assert len(found) == 1
                row = found[0]
                assert row["call_id"] == "c-1" and row["parse_error"]
                assert row["truncated_content"] == raw
                recovered_ids.append(row["item_id"])
                name, arguments = (
                    "history::read_item",
                    json.dumps({"window_id": row["window_id"], "item_id": row["item_id"]}),
                )
            else:
                assert index in (5, 9)
                recovered = json.loads(outputs[-1])
                assert recovered["text"] == raw
                assert recovered["input_kind"] == "json" and recovered["parse_error"]
                name = arguments = None
            wire_name = compatible_tool_name(name) if name else None
            if mode == "responses":
                output = (
                    [
                        {
                            "type": "function_call",
                            "call_id": f"c-{index}",
                            "id": f"i-{index}",
                            "name": wire_name,
                            "arguments": arguments,
                        }
                    ]
                    if name
                    else [
                        {
                            "type": "message",
                            "id": f"m-{index}",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "done"}],
                        }
                    ]
                )
                packet = {
                    "type": "response.completed",
                    "response": {"id": f"r-{index}", "output": output},
                }
            else:
                delta = (
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": f"c-{index}",
                                "type": "function",
                                "function": {"name": wire_name, "arguments": arguments},
                            }
                        ]
                    }
                    if name
                    else {"content": "done"}
                )
                packet = {
                    "choices": [
                        {
                            "index": 0,
                            "delta": delta,
                            "finish_reason": "tool_calls" if name else "stop",
                        }
                    ]
                }
            return httpx.Response(200, text=f"data: {json.dumps(packet)}\n\n")

        actual_client = httpx.AsyncClient

        def client_factory(*args, **kwargs):
            client = actual_client(*args, **kwargs, transport=httpx.MockTransport(respond))
            clients.append(client)
            return client

        monkeypatch.setattr(httpx, "AsyncClient", client_factory)

        def create(thread=None):
            registry = ToolRegistry()
            registry.register(guarded)
            return LangGraphRuntime.create(
                settings=settings(
                    tmp_path, api_mode=mode, api_key="fixture", tool_namespace_mode="compatible"
                ),
                database_path=tmp_path / "sessions.db",
                registry=registry,
                thread_id=thread,
            )

        runtime = create()
        thread = runtime.thread_id
        try:
            assert isinstance(
                [e async for e in runtime.stream("exercise recovery")][-1], TurnCompleted
            )
        finally:
            await runtime.aclose()
        cold = create(thread)
        try:
            assert isinstance([e async for e in cold.stream("continue")][-1], TurnCompleted)
            history = await cold._repository.load_items(thread)
            originals = [
                i for i in history if isinstance(i, ToolCallItem) and i.call.name == "guarded"
            ]
            assert len(originals) == 1 and originals[0].call.raw_arguments == raw
            assert recovered_ids == [str(originals[0].id)] * 2
            errors = [
                i for i in history if isinstance(i, ToolResultItem) and i.tool_name == "guarded"
            ]
            assert len(errors) == 1 and errors[0].is_error
            assert guarded.calls == 0 and len(payloads) == 9
        finally:
            await cold.aclose()
        assert len(clients) == 2 and all(client.is_closed for client in clients)

    asyncio.run(scenario())


def test_automatic_small_window_reset_recovers_a_target_near_end_of_large_output(tmp_path):
    from corki.protocol.events import ContextCompacted
    from corki.protocol.tools import ToolResult, ToolSpec

    async def scenario():
        requests = []

        class Large:
            spec = ToolSpec("long_output", "Large output fixture", {"type": "object"})

            async def execute(self, call, context):
                return ToolResult(call.id, call.name, "padding " * 10000 + "RECOVERY_TARGET: value")

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn = request.items[-1].turn_id
                index = len(requests)
                if index == 1:
                    name, args = "long_output", {}
                elif index == 2:
                    assert "ORIGINAL_TASK" not in str(request.items)
                    assert "padding padding" not in str(request.items)
                    name, args = (
                        "history::search_contents",
                        {"query": "RECOVERY_TARGET", "role": "tool", "tool_name": "long_output"},
                    )
                elif index == 3:
                    result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    found = json.loads(result.content)["items"][0]
                    assert "RECOVERY_TARGET" in found["truncated_content"]
                    name, args = (
                        "history::read_item",
                        {
                            "window_id": found["window_id"],
                            "item_id": found["item_id"],
                            "offset_chars": found["match_offset_chars"],
                            "limit_chars": 100,
                        },
                    )
                else:
                    result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    assert json.loads(result.content)["text"] == "RECOVERY_TARGET: value"
                    yield ModelCompleted((AssistantMessageItem("recovered", turn, new_step_id()),))
                    return
                yield ModelCompleted(
                    (ToolCallItem(ToolCall(new_tool_call_id(), name, args), turn, new_step_id()),)
                )

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Large())
        runtime = LangGraphRuntime.create(
            settings=settings(
                tmp_path,
                context_window_tokens=16000,
                auto_compact_tokens=12000,
                tool_output_token_limit=22500,
            ),
            database_path=tmp_path / "sessions.db",
            registry=registry,
            model=Model(),
        )
        try:
            events = [e async for e in runtime.stream("ORIGINAL_TASK")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert sum(isinstance(e, ContextCompacted) for e in events) == 1
            assert len(requests) == 4
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_another_thread_cannot_read_notes_or_archive_even_with_spoofed_context(tmp_path):
    async def scenario():
        model = SequenceModel(
            [
                (
                    "notes::write_file",
                    {"path": "p", "text": "PRIVATE_NOTE", "context": {"session_id": "spoofed"}},
                )
            ]
        )
        first = LangGraphRuntime.create(
            settings=settings(tmp_path),
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
            model=model,
        )
        try:
            assert isinstance(
                [e async for e in first.stream("PRIVATE_USER_TASK")][-1], TurnCompleted
            )
        finally:
            await first.aclose()
        model = SequenceModel(
            [
                (
                    "notes::read_file",
                    {"path": "/root/notes/p", "context": {"session_id": str(first.thread_id)}},
                ),
                (
                    "history::search_contents",
                    {
                        "query": "PRIVATE_USER_TASK",
                        "role": "user",
                        "context": {"session_id": str(first.thread_id)},
                    },
                ),
            ]
        )
        other = LangGraphRuntime.create(
            settings=settings(tmp_path),
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
            model=model,
        )
        try:
            assert isinstance([e async for e in other.stream("inspect")][-1], TurnCompleted)
            results = [i for i in model.requests[-1].items if isinstance(i, ToolResultItem)]
            assert results[0].is_error and "does not exist" in results[0].content
            assert json.loads(results[1].content)["items"] == []
            assert "PRIVATE_NOTE" not in str(model.requests)
        finally:
            await other.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("after_commit", [False, True])
def test_local_write_survives_history_fault_without_repeating_side_effect(
    tmp_path, monkeypatch, after_commit
):
    from corki.history_notes.notes_store import NotesStore
    from corki.protocol.events import TurnFailed
    from corki.sessions.models import TurnStatus

    async def scenario():
        writes = []
        original = NotesStore.call

        def observe(self, action, arguments, budget):
            if action == "append_to_file":
                writes.append(arguments)
            return original(self, action, arguments, budget)

        monkeypatch.setattr(NotesStore, "call", observe)
        model = SequenceModel([("notes::append_to_file", {"path": "p", "text": "once\n"})])

        def create(thread=None):
            return LangGraphRuntime.create(
                settings=settings(tmp_path),
                database_path=tmp_path / "sessions.db",
                registry=ToolRegistry(),
                model=model,
                thread_id=thread,
            )

        runtime = create()
        thread = runtime.thread_id
        append_items, save_turn = runtime._repository.append_items, runtime._repository.save_turn
        injected = False

        async def append(target, items):
            nonlocal injected
            if not injected and any(isinstance(i, ToolResultItem) for i in items):
                injected = True
                if after_commit:
                    await append_items(target, items)
                raise OSError("history fixture")
            await append_items(target, items)

        async def save(turn):
            if turn.status is TurnStatus.FAILED:
                raise OSError("terminal fixture")
            await save_turn(turn)

        runtime._repository.append_items, runtime._repository.save_turn = append, save
        try:
            assert isinstance([e async for e in runtime.stream("append")][-1], TurnFailed)
            assert len(writes) == len(model.requests) == 1
        finally:
            await runtime.aclose()
        cold = create(thread)
        try:
            assert isinstance([e async for e in cold.resume_pending()][-1], TurnCompleted)
            assert len(writes) == 1 and len(model.requests) == 2
            results = [
                i
                for i in await cold._repository.load_items(thread)
                if isinstance(i, ToolResultItem)
            ]
            assert len(results) == 1
            model.actions.append(("notes::read_file", {"path": "p"}))
            assert isinstance([e async for e in cold.stream("verify")][-1], TurnCompleted)
            result = [i for i in model.requests[-1].items if isinstance(i, ToolResultItem)][-1]
            assert json.loads(result.content)["text"] == "once\n"
        finally:
            await cold.aclose()

    asyncio.run(scenario())


def test_cancelling_sqlite_write_joins_worker_before_turn_ends(tmp_path, monkeypatch):
    import threading

    from corki.history_notes.notes_store import NotesStore
    from corki.protocol.events import TurnCancelled

    async def scenario():
        entered, release = threading.Event(), threading.Event()
        writes, events = [], []
        original = NotesStore.call

        def delayed(self, action, arguments, budget):
            if action == "append_to_file":
                entered.set()
                assert release.wait(5)
                writes.append(arguments)
            return original(self, action, arguments, budget)

        monkeypatch.setattr(NotesStore, "call", delayed)
        model = SequenceModel([("notes::append_to_file", {"path": "p", "text": "once\n"})])
        runtime = LangGraphRuntime.create(
            settings=settings(tmp_path),
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
            model=model,
        )

        async def consume():
            async for event in runtime.stream("append"):
                events.append(event)

        task = asyncio.create_task(consume())
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            await runtime.cancel_active()
            await asyncio.sleep(0.02)
            assert not task.done() and not writes
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
            assert isinstance(events[-1], TurnCancelled) and len(writes) == 1
            model.actions.append(("notes::read_file", {"path": "p"}))
            assert isinstance([e async for e in runtime.stream("verify")][-1], TurnCompleted)
            result = [i for i in model.requests[-1].items if isinstance(i, ToolResultItem)][-1]
            assert json.loads(result.content)["text"] == "once\n"
            assert len(writes) == 1
        finally:
            release.set()
            await runtime.aclose()

    asyncio.run(scenario())


def test_optional_local_store_failure_remains_an_observation(tmp_path):
    async def scenario():
        (tmp_path / "sessions.history-notes.db").mkdir()
        model = SequenceModel([("notes::write_file", {"path": "p", "text": "value"})])
        runtime = LangGraphRuntime.create(
            settings=settings(tmp_path),
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
            model=model,
        )
        try:
            assert isinstance([e async for e in runtime.stream("write")][-1], TurnCompleted)
            result = next(i for i in model.requests[-1].items if isinstance(i, ToolResultItem))
            assert result.is_error and "storage operation failed" in result.content
            assert str(tmp_path) not in result.content
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_plaintext_recovery_survives_reset_and_cold_runtime(tmp_path):
    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                assert request.client_metadata is None
                assert "history::search_contents" in {s.name for s in request.tools}
                turn = request.items[-1].turn_id
                index = len(requests)
                results = [i for i in request.items if isinstance(i, ToolResultItem)]
                if index == 1:
                    name, args = (
                        "notes::write_file",
                        {"path": "progress.md", "text": "saved\nnext\n"},
                    )
                elif index == 2:
                    name, args = "new_context", {}
                elif index == 3:
                    assert "ORIGINAL_SECRET_TASK" not in str(request.items)
                    name, args = (
                        "history::search_contents",
                        {"query": "ORIGINAL_SECRET_TASK", "role": "user"},
                    )
                elif index == 4:
                    found = json.loads(results[-1].content)["items"][0]
                    name, args = (
                        "history::read_item",
                        {"item_id": found["item_id"], "window_id": found["window_id"]},
                    )
                elif index == 5:
                    assert "ORIGINAL_SECRET_TASK" in json.loads(results[-1].content)["text"]
                    name, args = "notes::read_file", {"path": "progress.md", "start_line": -1}
                elif index == 6:
                    assert json.loads(results[-1].content)["text"] == "next\n"
                    yield ModelCompleted((AssistantMessageItem("done", turn, new_step_id()),))
                    return
                elif index == 7:
                    name, args = "notes::read_file", {"path": "/root/notes/progress.md"}
                else:
                    assert json.loads(results[-1].content)["text"] == "saved\nnext\n"
                    yield ModelCompleted((AssistantMessageItem("resumed", turn, new_step_id()),))
                    return
                yield ModelCompleted(
                    (ToolCallItem(ToolCall(new_tool_call_id(), name, args), turn, new_step_id()),)
                )

            async def aclose(self):
                pass

        model = Model()

        def create(thread=None):
            return LangGraphRuntime.create(
                settings=settings(tmp_path),
                database_path=tmp_path / "sessions.db",
                model=model,
                registry=ToolRegistry(),
                thread_id=thread,
            )

        runtime = create()
        thread = runtime.thread_id
        try:
            assert isinstance(
                [e async for e in runtime.stream("ORIGINAL_SECRET_TASK")][-1], TurnCompleted
            )
        finally:
            await runtime.aclose()
        cold = create(thread)
        try:
            assert isinstance([e async for e in cold.stream("continue")][-1], TurnCompleted)
            assert len(requests) == 8
        finally:
            await cold.aclose()

    asyncio.run(scenario())
