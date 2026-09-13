import asyncio
import json
import sqlite3
from contextlib import asynccontextmanager

import httpx
import pytest

from corki import http_client
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.core import runtime as runtime_module
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.models import resolve_capabilities
from corki.models.responses import OpenAIResponsesModel
from corki.protocol.events import AssistantTextDelta, TurnCompleted
from corki.protocol.ids import new_turn_id
from corki.protocol.items import UserMessageItem
from corki.protocol.tools import ToolResult, ToolSpec
from corki.sessions import TurnRecord, TurnStatus
from corki.tools import ToolRegistry


def packet(call=None, *, token="routing", usage=1):
    output = [{"type": "function_call", "name": "guard", "arguments": "{}", "call_id": call}]
    if call is None:
        output = [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "done"}],
            }
        ]
    event = {
        "type": "response.completed",
        "response": {
            "id": "response",
            "output": output,
            "usage": {"input_tokens": 100, "output_tokens": usage, "total_tokens": 100 + usage},
        },
    }
    return httpx.Response(
        200, headers={"x-codex-turn-state": token}, text=f"data: {json.dumps(event)}\n\n"
    )


def registry(executions):
    class Guard:
        spec = ToolSpec("guard", "guard", {"type": "object"})

        async def execute(self, call, context):
            executions.append(call.id)
            return ToolResult(call.id, call.name, "observation")

    tools = ToolRegistry()
    tools.register(Guard())
    return tools


def settings(tmp_path, **kwargs):
    return CorkiSettings(
        tmp_path,
        api_mode="responses",
        provider_name="custom",
        api_key="fixture",
        api_base="https://fixture.invalid/v1",
        skills_enabled=False,
        model_retry_base_seconds=0.001,
        model_request_max_retries=0,
        **kwargs,
    )


def test_shared_adapter_does_not_share_logical_turn_state(tmp_path):
    async def scenario():
        first_requests, requests, executions = set(), [], []
        both_started = asyncio.Event()

        async def respond(request):
            body = json.loads(request.content)
            branch = "branch-A" if "branch-A" in json.dumps(body) else "branch-B"
            requests.append((branch, request.headers.get("x-codex-turn-state")))
            if branch not in first_requests:
                first_requests.add(branch)
                if len(first_requests) == 2:
                    both_started.set()
                await asyncio.wait_for(both_started.wait(), 3)
                return packet(f"call-{branch}", token=f"state-{branch}")
            return packet(token="replacement-state")

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            model = OpenAIResponsesModel(
                api_key="fixture",
                base_url="https://fixture.invalid/v1",
                capabilities=resolve_capabilities(
                    base_url="https://fixture.invalid/v1", api_mode="responses"
                ),
                client=client,
            )
            runtimes = [
                LangGraphRuntime.create(
                    settings=settings(tmp_path),
                    model=model,
                    database_path=tmp_path / f"{branch}.db",
                    registry=registry(executions),
                )
                for branch in ("A", "B")
            ]

            async def consume(runtime, branch):
                return [event async for event in runtime.stream(branch)]

            try:
                results = await asyncio.gather(
                    *(
                        consume(runtime, branch)
                        for runtime, branch in zip(runtimes, ("branch-A", "branch-B"), strict=True)
                    )
                )
                assert all(isinstance(events[-1], TurnCompleted) for events in results)
                for branch in ("branch-A", "branch-B"):
                    assert [value for name, value in requests if name == branch] == [
                        None,
                        None,
                    ]
                assert sorted(executions) == ["call-branch-A", "call-branch-B"]
            finally:
                await asyncio.gather(*(runtime.aclose() for runtime in runtimes))

    asyncio.run(scenario())


@pytest.mark.parametrize("bootstrap_busy", [False, True])
def test_pending_checkpoint_cold_resume_does_not_restore_routing_token(
    tmp_path, monkeypatch, bootstrap_busy
):
    async def scenario():
        requests, executions = [], []

        def respond(request):
            requests.append(request.headers.get("x-codex-turn-state"))
            return packet("cold-call" if len(requests) == 1 else None, token="private-cold-token")

        real = httpx.AsyncClient
        monkeypatch.setattr(
            http_client,
            "OwnedHTTPClient",
            lambda *a, **kw: real(*a, **kw, transport=httpx.MockTransport(respond)),
        )
        config = settings(tmp_path)

        def create(thread=None):
            return LangGraphRuntime.create(
                settings=config,
                database_path=tmp_path / "s.db",
                thread_id=thread,
                registry=registry(executions),
            )

        class Sink:
            async def emit(self, event):
                pass

        runtime = create()
        try:
            await runtime._ensure_ready()
            thread, turn = runtime.thread_id, new_turn_id()
            await runtime._repository.save_turn(
                TurnRecord(turn, thread, TurnStatus.RUNNING, "use guard")
            )
            await runtime._compiled.ainvoke(
                _initial_state(thread, turn, config, UserMessageItem("use guard", turn)),
                config=runtime._graph_config(turn),
                context=GraphRunContext(events=Sink()),
                interrupt_after=["execute_tools"],
            )
            checkpoint = await runtime._compiled.aget_state(runtime._graph_config(turn))
            assert "private-cold-token" not in repr(checkpoint.values)
            assert executions == ["cold-call"]
            await runtime.aclose()
            setup_attempts = []
            if bootstrap_busy:
                factory = runtime_module.AsyncSqliteSaver.from_conn_string

                @asynccontextmanager
                async def context(path):
                    async with factory(path) as saver:
                        original_setup = saver.setup

                        async def setup():
                            setup_attempts.append(True)
                            if len(setup_attempts) == 1:
                                error = sqlite3.OperationalError("cold bootstrap busy")
                                error.sqlite_errorcode = sqlite3.SQLITE_BUSY
                                raise error
                            await original_setup()

                        saver.setup = setup
                        yield saver

                monkeypatch.setattr(runtime_module.AsyncSqliteSaver, "from_conn_string", context)
            runtime = create(thread)
            assert isinstance([e async for e in runtime.resume_pending()][-1], TurnCompleted)
            assert requests == [None, None]
            assert executions == ["cold-call"]
            if bootstrap_busy:
                assert len(setup_attempts) >= 2
            assert "private-cold-token" not in repr(await runtime._repository.load_items(thread))
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_local_compaction_has_its_own_routing_session(tmp_path, monkeypatch):
    async def scenario():
        requests, executions = [], []
        compact_attempts = 0

        def respond(request):
            nonlocal compact_attempts
            body = json.loads(request.content)
            local = "tools" not in body
            requests.append((local, request.headers.get("x-codex-turn-state")))
            if len(requests) == 1:
                return packet("local-call", token="main-state", usage=2000)
            if local:
                compact_attempts += 1
                if compact_attempts == 1:
                    return httpx.Response(
                        200, text="", headers={"x-codex-turn-state": "local-state"}
                    )
                return packet(token="ignored-local-state")
            return packet(token="ignored-main-state")

        real = httpx.AsyncClient
        monkeypatch.setattr(
            http_client,
            "OwnedHTTPClient",
            lambda *a, **kw: real(*a, **kw, transport=httpx.MockTransport(respond)),
        )
        runtime = LangGraphRuntime.create(
            settings=settings(tmp_path, context_window_tokens=20000, auto_compact_tokens=1800),
            database_path=tmp_path / "s.db",
            registry=registry(executions),
        )
        try:
            assert isinstance([e async for e in runtime.stream("use guard")][-1], TurnCompleted)
            assert requests == [
                (False, None),
                (True, None),
                (True, None),
                (False, None),
            ]
            assert executions == ["local-call"]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("close", [False, True])
def test_cancelled_stream_routing_state_cannot_reach_next_turn(tmp_path, monkeypatch, close):
    async def scenario():
        requests = []
        entered, cleaned = asyncio.Event(), asyncio.Event()

        class Slow(httpx.AsyncByteStream):
            async def __aiter__(self):
                entered.set()
                await asyncio.Event().wait()
                yield b"unreachable"

            async def aclose(self):
                cleaned.set()

        def respond(request):
            requests.append(request.headers.get("x-codex-turn-state"))
            if len(requests) == 1:
                return httpx.Response(
                    200, headers={"x-codex-turn-state": "cancelled-state"}, stream=Slow()
                )
            return packet(token="next-state")

        real = httpx.AsyncClient
        monkeypatch.setattr(
            http_client,
            "OwnedHTTPClient",
            lambda *a, **kw: real(*a, **kw, transport=httpx.MockTransport(respond)),
        )

        def create(thread=None):
            return LangGraphRuntime.create(
                settings=settings(tmp_path),
                database_path=tmp_path / "s.db",
                registry=registry([]),
                thread_id=thread,
            )

        runtime = create()
        thread = runtime.thread_id

        async def consume():
            return [e async for e in runtime.stream("interrupted")]

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(entered.wait(), 3)
            await (runtime.aclose() if close else runtime.cancel_active())
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
            assert cleaned.is_set()
            if close:
                runtime = create(thread)
            assert isinstance([e async for e in runtime.stream("next turn")][-1], TurnCompleted)
            assert requests == [None, None]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


def test_steering_reuses_current_turn_routing_state(tmp_path, monkeypatch):
    async def scenario():
        requests = []
        closed = asyncio.Event()
        release = asyncio.Event()

        class Partial(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'data: {"type":"response.output_text.delta","delta":"old prefix"}\n\n'
                await release.wait()
                yield b'data: {"type":"response.completed","response":{"id":"first"}}\n\n'

            async def aclose(self):
                closed.set()

        def respond(request):
            requests.append(request.headers.get("x-codex-turn-state"))
            if len(requests) == 1:
                return httpx.Response(
                    200, headers={"x-codex-turn-state": "steering-state"}, stream=Partial()
                )
            return packet(token="later-state")

        real = httpx.AsyncClient
        monkeypatch.setattr(
            http_client,
            "OwnedHTTPClient",
            lambda *a, **kw: real(*a, **kw, transport=httpx.MockTransport(respond)),
        )
        runtime = LangGraphRuntime.create(
            settings=settings(tmp_path), database_path=tmp_path / "s.db", registry=registry([])
        )

        async def consume():
            events = []
            async for event in runtime.stream("original", realtime=True):
                events.append(event)
                if isinstance(event, AssistantTextDelta) and event.delta == "old prefix":
                    await runtime.steer("new direction")
                    assert not closed.is_set()
                    release.set()
            return events

        try:
            events = await asyncio.wait_for(consume(), 3)
            assert isinstance(events[-1], TurnCompleted)
            assert requests == [None, None]
            assert closed.is_set()
        finally:
            release.set()
            await runtime.aclose()

    asyncio.run(scenario())
