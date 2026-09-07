import asyncio
import json

import httpx
import pytest

from corki.config import CorkiSettings, TokenBudgetConfig
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


def native_settings(tmp_path, **changes):
    from dataclasses import replace

    return replace(
        CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            api_mode="responses",
            provider_name="openai",
            api_key="backend-fixture",
            api_base="https://fixture.invalid/backend-api/codex",
            codex_backend=True,
            token_budget_enabled=True,
            token_budget=TokenBudgetConfig(use_history_notes_extension=True),
        ),
        **changes,
    )


class ActionModel:
    def __init__(self, actions=()):
        self.actions = list(actions)
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        turn = request.items[-1].turn_id
        if self.actions:
            name, arguments = self.actions.pop(0)
            item = ToolCallItem(ToolCall(new_tool_call_id(), name, arguments), turn, new_step_id())
        else:
            item = AssistantMessageItem("done", turn, new_step_id())
        yield ModelCompleted((item,))

    async def aclose(self):
        pass


def test_overall_backend_deadline_closes_a_stalled_stream(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from corki.history_notes import backend
    from corki.protocol.items import ToolResultItem

    async def scenario():
        limits, closed = [], []

        def timeout(seconds):
            limits.append(seconds)
            return asyncio.timeout(0.01)

        monkeypatch.setattr(backend, "asyncio", SimpleNamespace(timeout=timeout))

        class Slow(httpx.AsyncByteStream):
            async def __aiter__(self):
                await asyncio.Event().wait()
                yield b"unreachable"

            async def aclose(self):
                closed.append(True)

        def respond(request):
            if request.url.path.endswith("/thread_hint"):
                return httpx.Response(200, json={"text": ""})
            return httpx.Response(200, stream=Slow())

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        model = ActionModel([("notes::append_to_file", {"path": "p", "text": "opaque"})])
        runtime = LangGraphRuntime.create(
            settings=native_settings(tmp_path),
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
            model=model,
            history_notes_client=client,
        )
        try:
            assert isinstance([e async for e in runtime.stream("append")][-1], TurnCompleted)
            result = next(i for i in model.requests[1].items if isinstance(i, ToolResultItem))
            assert result.is_error and "outcome may be unknown" in result.content
            assert limits == [35, 35] and closed == [True]
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("tool_mode", ["direct", "code_mode_only"])
def test_native_notes_write_reset_read_and_trusted_identity(tmp_path, tool_mode):
    async def scenario():
        requests, backend_calls, notes = [], [], {}

        def respond(request):
            body = json.loads(request.content)
            backend_calls.append((request, body))
            action = request.url.path.rsplit("/", 1)[-1]
            if action == "thread_hint":
                return httpx.Response(200, json={"text": "NATIVE_NOTE_HINT"})
            if action == "write_file":
                notes[body["path"]] = body["text"]
                return httpx.Response(200, json={"ok": True})
            assert action == "read_file"
            return httpx.Response(200, json={"encrypted_output": notes[body["path"]]})

        class Model:
            async def stream(self, request):
                requests.append(request)
                index = len(requests)
                turn = request.items[-1].turn_id
                names = {spec.name for spec in request.tools}
                assert "notes::write_file" in names and "history::read_item" in names
                if index <= 3:
                    name, args = (
                        (
                            "notes::write_file",
                            {
                                "path": "progress.md",
                                "text": "opaque-note",
                                "context": {"session_id": "spoofed", "current_agent_name": "/bad"},
                            },
                        )
                        if index == 1
                        else ("new_context", {})
                        if index == 2
                        else ("notes::read_file", {"path": "progress.md"})
                    )
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(new_tool_call_id(), name, args), turn, new_step_id()
                            ),
                        )
                    )
                else:
                    yield ModelCompleted((AssistantMessageItem("done", turn, new_step_id()),))

            async def aclose(self):
                pass

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                api_mode="responses",
                provider_name="openai",
                api_key="backend-fixture",
                api_base="https://fixture.invalid/backend-api/codex",
                codex_backend=True,
                token_budget_enabled=True,
                tool_mode=tool_mode,
                token_budget=TokenBudgetConfig(use_history_notes_extension=True),
            ),
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
            model=Model(),
            history_notes_client=client,
        )
        try:
            events = [event async for event in runtime.stream("ORIGINAL_TASK")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(requests) == 4
            assert "ORIGINAL_TASK" not in str(requests[2].items)
            assert [request.url.path.rsplit("/", 1)[-1] for request, _ in backend_calls] == [
                "thread_hint",
                "write_file",
                "thread_hint",
                "read_file",
            ]
            for request, body in backend_calls:
                assert body["context"] == {
                    "session_id": str(runtime.thread_id),
                    "current_agent_name": "/root",
                }
                assert request.headers["authorization"] == "Bearer backend-fixture"
                assert request.extensions["timeout"]["read"] == 35
            before = json.loads(requests[0].client_metadata["x-codex-turn-metadata"])
            after = json.loads(requests[2].client_metadata["x-codex-turn-metadata"])
            assert before["window_number"] == 0 and after["window_number"] == 1
            assert before["context_window_id"] != after["context_window_id"]
            assert before["history_ingest_requested"] and after["agent_name"] == "/root"
            assert all(str(request.items).count("NATIVE_NOTE_HINT") == 1 for request in requests)
        finally:
            await runtime.aclose()
            assert not client.is_closed
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("after_commit", [False, True])
def test_native_write_result_recovers_without_repeating_backend(tmp_path, after_commit):
    from corki.protocol.events import TurnFailed
    from corki.protocol.items import ToolResultItem
    from corki.sessions.models import TurnStatus

    async def scenario():
        writes = []

        def respond(request):
            if request.url.path.endswith("/thread_hint"):
                return httpx.Response(200, json={"text": ""})
            writes.append(request)
            return httpx.Response(200, json={"encrypted_output": "COMMITTED_WRITE_RESULT"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        model = ActionModel([("notes::write_file", {"path": "p", "text": "opaque"})])

        def create(thread=None):
            return LangGraphRuntime.create(
                settings=native_settings(tmp_path),
                database_path=tmp_path / "sessions.db",
                registry=ToolRegistry(),
                model=model,
                history_notes_client=client,
                thread_id=thread,
            )

        runtime = create()
        thread = runtime.thread_id
        append_items, save_turn = runtime._repository.append_items, runtime._repository.save_turn
        injected = False

        async def append(target, items):
            nonlocal injected
            if not injected and any(isinstance(item, ToolResultItem) for item in items):
                injected = True
                if after_commit:
                    await append_items(target, items)
                raise OSError("history write fixture")
            await append_items(target, items)

        async def save(turn):
            if turn.status is TurnStatus.FAILED:
                raise OSError("terminal write fixture")
            await save_turn(turn)

        runtime._repository.append_items, runtime._repository.save_turn = append, save
        try:
            events = [e async for e in runtime.stream("save")]
            assert isinstance(events[-1], TurnFailed) and events[-1].error_kind == "storage"
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
            assert results[0].content_items[0].encrypted_content == "COMMITTED_WRITE_RESULT"
        finally:
            await cold.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("arguments", [{"path": 3}, {}, {"path": "p", "start_line": "last"}])
def test_invalid_tool_schema_never_reaches_backend(tmp_path, arguments):
    from corki.protocol.items import ToolResultItem

    async def scenario():
        calls = []

        def respond(request):
            calls.append(request.url.path)
            return httpx.Response(200, json={"text": ""})

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        model = ActionModel([("notes::read_file", arguments)])
        runtime = LangGraphRuntime.create(
            settings=native_settings(tmp_path),
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
            model=model,
            history_notes_client=client,
        )
        try:
            assert isinstance([e async for e in runtime.stream("read")][-1], TurnCompleted)
            assert next(
                i for i in model.requests[1].items if isinstance(i, ToolResultItem)
            ).is_error
            assert len(calls) == 1 and calls[0].endswith("/thread_hint")
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("namespace_mode", ["native", "compatible"])
def test_nine_actions_actual_responses_ingestion_and_cold_window(
    tmp_path, monkeypatch, namespace_mode
):
    from corki.protocol.tool_names import compatible_tool_name

    async def scenario():
        payloads, operations, clients = [], [], []
        actions = [
            ("history::list_windows", {"agent_name": None, "limit": 2}),
            ("history::list_items", {"role": "tool", "window_id": None}),
            ("history::read_item", {"item_id": "item-1", "window_id": "window-1"}),
            ("history::search_contents", {"query": "encrypted-query"}),
            ("notes::list_files_by_prefix", {"file_order": "descending"}),
            ("notes::read_file", {"path": "progress.md", "start_line": -1}),
            ("notes::search_contents", {"query": "encrypted-query"}),
            ("notes::append_to_file", {"path": "progress.md", "text": "encrypted-append"}),
            ("notes::write_file", {"path": "progress.md", "text": "encrypted-write"}),
        ]

        def respond(request):
            body = json.loads(request.content)
            if not request.url.path.endswith("/responses"):
                operations.append((request, body))
                if request.url.path.endswith("/thread_hint"):
                    return httpx.Response(200, json={"text": "NATIVE_HINT"})
                return httpx.Response(200, json={"encrypted_output": "OPAQUE_RESULT"})
            payloads.append(body)
            metadata = body["client_metadata"]
            for header in ("x-codex-window-id", "x-codex-turn-metadata"):
                assert request.headers[header] == metadata[header]
            assert "authorization" not in metadata
            index = len(payloads)
            selected = actions if index == 1 else [("new_context", {})] if index == 2 else []
            output = []
            for i, (name, arguments) in enumerate(selected):
                namespace, _, leaf = name.rpartition("::")
                output.append(
                    {
                        "type": "function_call",
                        "id": f"i-{index}-{i}",
                        "call_id": f"c-{index}-{i}",
                        "name": leaf if namespace_mode == "native" else compatible_tool_name(name),
                        **(
                            {"namespace": namespace}
                            if namespace and namespace_mode == "native"
                            else {}
                        ),
                        "arguments": json.dumps(arguments),
                    }
                )
            if not output:
                output = [
                    {
                        "type": "message",
                        "id": f"m-{index}",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "done"}],
                    }
                ]
            packet = {
                "type": "response.completed",
                "response": {"id": f"r-{index}", "output": output},
            }
            return httpx.Response(200, text=f"data: {json.dumps(packet)}\n\n")

        real_client = httpx.AsyncClient

        def create_client(*args, **kwargs):
            client = real_client(*args, **kwargs, transport=httpx.MockTransport(respond))
            clients.append(client)
            return client

        monkeypatch.setattr(httpx, "AsyncClient", create_client)

        def create(thread=None):
            return LangGraphRuntime.create(
                settings=native_settings(tmp_path, tool_namespace_mode=namespace_mode),
                database_path=tmp_path / "sessions.db",
                registry=ToolRegistry(),
                thread_id=thread,
            )

        runtime = create()
        thread = runtime.thread_id
        try:
            assert isinstance([e async for e in runtime.stream("TASK")][-1], TurnCompleted)
            outputs = [i for i in payloads[1]["input"] if i.get("type") == "function_call_output"]
            assert len(outputs) == 9
            assert all(
                i["output"] == [{"type": "encrypted_content", "encrypted_content": "OPAQUE_RESULT"}]
                for i in outputs
            )
            first_meta = json.loads(payloads[0]["client_metadata"]["x-codex-turn-metadata"])
            reset_meta = json.loads(payloads[2]["client_metadata"]["x-codex-turn-metadata"])
            assert first_meta["window_number"] == 0 and reset_meta["window_number"] == 1
            assert first_meta["window_id"] == f"{thread}:0"
            assert reset_meta["window_id"] == f"{thread}:1"
            assert reset_meta["window_id"] != reset_meta["context_window_id"]
            assert first_meta["window_id"] != reset_meta["window_id"]
            assert "OPAQUE_RESULT" not in json.dumps(payloads[2])
        finally:
            await runtime.aclose()
        assert all(client.is_closed for client in clients)
        cold = create(thread)
        try:
            assert isinstance([e async for e in cold.stream("continue")][-1], TurnCompleted)
            cold_meta = json.loads(payloads[-1]["client_metadata"]["x-codex-turn-metadata"])
            assert cold_meta["window_id"] == reset_meta["window_id"]
            assert cold_meta["turn_id"] != reset_meta["turn_id"]
        finally:
            await cold.aclose()
        assert all(client.is_closed for client in clients)
        action_ops = [
            (req, body) for req, body in operations if not req.url.path.endswith("/thread_hint")
        ]
        assert len(action_ops) == 9
        assert {req.url.path for req, _ in action_ops} == {
            "/backend-api/codex/alpha/" + name.replace("::", "/v2/") for name, _ in actions
        }
        for request, body in operations:
            assert body["context"] == {"session_id": str(thread), "current_agent_name": "/root"}
            encrypted = request.url.path.endswith(
                ("/search_contents", "/append_to_file", "/write_file")
            )
            assert request.headers.get("x-openai-encrypted-tool-arguments") == (
                "true" if encrypted else None
            )
            limit = 4000 if request.url.path.endswith("/thread_hint") else 20000
            assert json.loads(request.headers["x-openai-tool-output-truncation-policy"]) == {
                "mode": "bytes",
                "limit": limit,
            }

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure",
    [
        "401",
        "500",
        "timeout",
        "json",
        "images",
        "image_detail",
        "oversize",
        "nan",
        "overflow",
        "utf16",
    ],
)
def test_backend_failure_is_single_attempt_observation(tmp_path, failure):
    from corki.protocol.items import ToolResultItem

    async def scenario():
        operations = []

        def respond(request):
            if request.url.path.endswith("/thread_hint"):
                return httpx.Response(200, json={"text": ""})
            operations.append(request)
            if failure in ("401", "500"):
                return httpx.Response(int(failure), text="SECRET_SERVER_DIAGNOSTIC")
            if failure == "timeout":
                raise httpx.ReadTimeout("SECRET_SERVER_DIAGNOSTIC", request=request)
            if failure == "json":
                return httpx.Response(200, text="SECRET_SERVER_DIAGNOSTIC")
            if failure == "oversize":
                return httpx.Response(200, content=b"x" * 32_000_001)
            if failure in ("nan", "overflow", "utf16"):
                content = {
                    "nan": b'{"value":NaN}',
                    "overflow": b'{"value":1e999}',
                    "utf16": '{"text":"not utf8"}'.encode("utf-16"),
                }[failure]
                return httpx.Response(200, content=content)
            return httpx.Response(
                200,
                json={
                    "images": None
                    if failure == "images"
                    else [{"data": "YWJj", "mime_type": "image/png", "detail": "bogus"}]
                },
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        model = ActionModel([("notes::write_file", {"path": "p", "text": "opaque"})])
        runtime = LangGraphRuntime.create(
            settings=native_settings(tmp_path),
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
            model=model,
            history_notes_client=client,
        )
        try:
            assert isinstance([e async for e in runtime.stream("run")][-1], TurnCompleted)
            result = next(i for i in model.requests[1].items if isinstance(i, ToolResultItem))
            assert result.is_error
            assert "SECRET_SERVER_DIAGNOSTIC" not in str(result)
            assert "backend-fixture" not in str(result)
            assert len(operations) == 1
            assert not (tmp_path / "sessions.history-notes.db").exists()
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("encrypted", [False, True])
def test_backend_images_are_separate_and_hint_exact_byte_limit_is_accepted(tmp_path, encrypted):
    import base64
    import io

    from PIL import Image

    from corki.protocol.items import ToolResultItem
    from corki.protocol.tools import EncryptedContent, ImageAttachment, TextContent

    async def scenario():
        buffer = io.BytesIO()
        Image.new("RGB", (1, 1), "red").save(buffer, format="PNG")
        data = base64.b64encode(buffer.getvalue()).decode()
        hint = "界" * 1333 + "."

        def respond(request):
            if request.url.path.endswith("/thread_hint"):
                return httpx.Response(200, json={"text": hint})
            body = {"encrypted_output": "opaque"} if encrypted else {"text": "visible"}
            return httpx.Response(
                200, json={**body, "images": [{"data": data, "mime_type": "image/png"}]}
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        model = ActionModel([("history::read_item", {"item_id": "i", "window_id": "w"})])
        runtime = LangGraphRuntime.create(
            settings=native_settings(tmp_path),
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
            model=model,
            history_notes_client=client,
        )
        try:
            assert isinstance([e async for e in runtime.stream("read")][-1], TurnCompleted)
            assert hint in str(model.requests[0].items)
            result = next(i for i in model.requests[1].items if isinstance(i, ToolResultItem))
            assert not result.is_error
            assert len(result.content_items) == 2
            assert isinstance(
                result.content_items[0], EncryptedContent if encrypted else TextContent
            )
            if not encrypted:
                assert json.loads(result.content_items[0].text) == {"text": "visible"}
            assert isinstance(result.content_items[1], ImageAttachment)
            assert result.content_items[1].data_url == "data:image/png;base64," + data
            assert data not in result.content
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "override",
    [
        {"codex_backend": False},
        {"api_key": None},
        {"provider_name": "other"},
        {"api_mode": "chat_completions"},
        {"token_budget_enabled": False},
        {"token_budget": TokenBudgetConfig()},
    ],
)
def test_ineligible_runtime_never_uses_native_backend_or_ingest(tmp_path, override):
    async def scenario():
        calls = []

        def respond(request):
            calls.append(request)
            return httpx.Response(500)

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        model = ActionModel()
        runtime = LangGraphRuntime.create(
            settings=native_settings(tmp_path, **override),
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
            model=model,
            history_notes_client=client,
        )
        try:
            assert isinstance([e async for e in runtime.stream("run")][-1], TurnCompleted)
            assert not calls
            assert model.requests[0].client_metadata is None
            requested = (
                runtime._settings.token_budget_enabled
                and runtime._settings.token_budget.use_history_notes_extension
            )
            assert any(
                s.name.startswith(("history::", "notes::")) for s in model.requests[0].tools
            ) is bool(requested)
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("hint", [None, {"text": 4}, {"text": "界" * 1334}, {"text": ""}])
def test_bad_new_window_hint_does_not_retain_old_hint(tmp_path, hint):
    async def scenario():
        hints = []

        def respond(request):
            hints.append(request)
            if len(hints) == 1:
                return httpx.Response(200, json={"text": "OLD_HINT"})
            return httpx.Response(500) if hint is None else httpx.Response(200, json=hint)

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        model = ActionModel([("new_context", {})])
        runtime = LangGraphRuntime.create(
            settings=native_settings(tmp_path),
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
            model=model,
            history_notes_client=client,
        )
        try:
            assert isinstance([e async for e in runtime.stream("run")][-1], TurnCompleted)
            assert "OLD_HINT" in str(model.requests[0].items)
            assert "OLD_HINT" not in str(model.requests[1].items)
            assert isinstance([e async for e in runtime.stream("again")][-1], TurnCompleted)
            assert len(hints) == 2
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("during_hint", [True, False])
def test_cancel_http_operation_releases_stream_without_retry(tmp_path, during_hint):
    from corki.protocol.events import TurnCancelled

    async def scenario():
        entered, released = asyncio.Event(), asyncio.Event()
        calls = []

        class Pending(httpx.AsyncByteStream):
            async def __aiter__(self):
                entered.set()
                await asyncio.Event().wait()
                yield b"unreachable"

            async def aclose(self):
                released.set()

        def respond(request):
            if not during_hint and request.url.path.endswith("/thread_hint"):
                return httpx.Response(200, json={"text": ""})
            calls.append(request)
            return httpx.Response(200, stream=Pending())

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        model = ActionModel([("notes::append_to_file", {"path": "p", "text": "opaque"})])
        runtime = LangGraphRuntime.create(
            settings=native_settings(tmp_path),
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
            model=model,
            history_notes_client=client,
        )
        events = []

        async def consume():
            async for event in runtime.stream("run"):
                events.append(event)

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(entered.wait(), 5)
            await runtime.cancel_active()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
            assert isinstance(events[-1], TurnCancelled)
            assert released.is_set() and len(calls) == 1
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())
