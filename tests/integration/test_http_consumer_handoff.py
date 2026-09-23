"""Actual model/compaction owners must reclaim pre-response connections."""

import asyncio

import pytest
from anyio.abc import SocketAttribute
from anyio.lowlevel import get_async_backend

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import (
    ModelRequest,
    OpenAICompatibleModel,
    OpenAIResponsesModel,
    resolve_capabilities,
)
from corki.protocol.events import ContextCompacted, TurnCancelled, TurnCompleted, TurnFailed
from corki.protocol.ids import new_turn_id
from corki.protocol.items import CompactionItem, UserMessageItem
from corki.tools import ToolRegistry


@pytest.mark.parametrize("consumer", ["chat", "responses", "summary_chat", "summary_responses"])
@pytest.mark.parametrize("window", ["tcp_handoff", "tls_handshake"])
def test_owned_http_consumer_cancellation_reclaims_socket(monkeypatch, tmp_path, consumer, window):
    async def scenario():
        streams, writers, peers, events = [], [], set(), []
        handshake, peer_closed = asyncio.Event(), asyncio.Event()
        operation = None
        runtime = None

        async def peer(reader, writer):
            task = asyncio.current_task()
            peers.add(task)
            writers.append(writer)
            try:
                if window == "tls_handshake":
                    assert await reader.read(4096)  # Actual TLS ClientHello.
                    handshake.set()
                await reader.read()
                peer_closed.set()
            finally:
                writer.close()
                await writer.wait_closed()
                peers.discard(task)

        backend = get_async_backend()
        original_connect = backend.connect_tcp

        async def connect(*args, **kwargs):
            stream = await original_connect(*args, **kwargs)
            streams.append(stream)
            if window == "tcp_handoff":
                # Cancel at the real stream handoff, with no inserted await.
                operation.cancel()
            return stream

        monkeypatch.setattr(backend, "connect_tcp", connect)
        monkeypatch.setenv("NO_PROXY", "*")
        monkeypatch.setenv("no_proxy", "*")
        server = await asyncio.start_server(peer, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        scheme = "https" if window == "tls_handshake" else "http"
        base = f"{scheme}://127.0.0.1:{port}"
        api_mode = "chat_completions" if consumer.endswith("chat") else "responses"
        model_type = OpenAICompatibleModel if consumer.endswith("chat") else OpenAIResponsesModel
        owner = model_type(
            api_key="fixture",
            base_url=base,
            capabilities=resolve_capabilities(base_url=base, api_mode=api_mode),
            max_retries=0,
            request_max_retries=0,
        )
        turn = new_turn_id()
        request = ModelRequest(
            "test",
            "system",
            (),
            (UserMessageItem("hello", turn),),
            (),
        )
        if consumer.startswith("summary_"):
            runtime = await LangGraphRuntime.acreate(
                settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
                database_path=tmp_path / "summary.db",
                model=owner,
                registry=ToolRegistry(),
                load_plugins=False,
            )
            await runtime._ensure_ready()
            await runtime._repository.append_items(runtime.thread_id, request.items)

        async def consume():
            stream = runtime.compact() if runtime else owner.stream(request)
            async for event in stream:
                events.append(event)

        try:
            operation = asyncio.create_task(consume())
            if window == "tls_handshake":
                await asyncio.wait_for(handshake.wait(), 2)
                operation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(operation, 2)
            if runtime:
                await runtime.aclose()
                assert not any(
                    isinstance(e, (TurnCompleted, TurnFailed, ContextCompacted)) for e in events
                )
                assert await runtime._repository.latest_running_turn(runtime.thread_id) is None
                stored = await runtime._repository.load_items(runtime.thread_id)
                assert stored[0] == request.items[0]
                assert not any(isinstance(item, CompactionItem) for item in stored)
            else:
                await owner.aclose()
                assert not events
            assert len(streams) == 1  # Cancellation must not become a retry.
            assert streams[0].extra(SocketAttribute.raw_socket).fileno() == -1
            await asyncio.wait_for(peer_closed.wait(), 1)
        finally:
            if operation is not None:
                operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)
            await owner.aclose()
            if runtime:
                await runtime.aclose()
            for stream in streams:
                await stream.aclose()
            for writer in writers:
                writer.close()
                await writer.wait_closed()
            server.close()
            await server.wait_closed()
            await asyncio.gather(*peers)

    asyncio.run(scenario())


@pytest.mark.parametrize("api_mode", ["chat_completions", "responses"])
@pytest.mark.parametrize("operation_kind", ["normal", "realtime", "summary"])
@pytest.mark.parametrize("action", ["cancel", "close"])
def test_runtime_owned_model_tls_cancel_has_one_terminal_and_no_socket(
    monkeypatch, tmp_path, api_mode, operation_kind, action
):
    async def scenario():
        streams, writers, peers, events = [], [], set(), []
        handshake, peer_closed = asyncio.Event(), asyncio.Event()

        async def peer(reader, writer):
            task = asyncio.current_task()
            peers.add(task)
            writers.append(writer)
            try:
                assert await reader.read(4096)
                handshake.set()
                await reader.read()
                peer_closed.set()
            finally:
                writer.close()
                await writer.wait_closed()
                peers.discard(task)

        backend = get_async_backend()
        original_connect = backend.connect_tcp

        async def connect(*args, **kwargs):
            stream = await original_connect(*args, **kwargs)
            streams.append(stream)
            return stream

        monkeypatch.setattr(backend, "connect_tcp", connect)
        monkeypatch.setenv("NO_PROXY", "*")
        monkeypatch.setenv("no_proxy", "*")
        server = await asyncio.start_server(peer, "127.0.0.1", 0)
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                api_base=f"https://127.0.0.1:{server.sockets[0].getsockname()[1]}",
                api_mode=api_mode,
                api_key="fixture",
                skills_enabled=False,
            ),
            database_path=tmp_path / "sessions.db",
            home_path=tmp_path / "home",
            registry=ToolRegistry(),
            load_plugins=False,
        )

        async def consume():
            stream = (
                runtime.compact()
                if operation_kind == "summary"
                else runtime.stream("hello", realtime=operation_kind == "realtime")
            )
            async for event in stream:
                events.append(event)

        operation = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(handshake.wait(), 3)
            await (runtime.cancel_active() if action == "cancel" else runtime.aclose())
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(operation), 3)
            assert sum(isinstance(e, TurnCancelled) for e in events) == 1
            assert not any(
                isinstance(e, (TurnCompleted, TurnFailed, ContextCompacted)) for e in events
            )
            assert not any(
                isinstance(item, CompactionItem)
                for item in await runtime._repository.load_items(runtime.thread_id)
            )
            assert await runtime._repository.latest_running_turn(runtime.thread_id) is None
            await runtime.aclose()
            assert len(streams) == 1
            assert streams[0].extra(SocketAttribute.raw_socket).fileno() == -1
            await asyncio.wait_for(peer_closed.wait(), 1)
            assert not any(
                t.get_name() in {"corki-model-read", "corki-steering-read", "corki-stop-read"}
                for t in asyncio.all_tasks()
            )
        finally:
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
            await runtime.aclose()
            for stream in streams:
                await stream.aclose()
            for writer in writers:
                writer.close()
                await writer.wait_closed()
            server.close()
            await server.wait_closed()
            await asyncio.gather(*peers)

    asyncio.run(scenario())
