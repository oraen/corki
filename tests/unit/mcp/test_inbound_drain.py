import asyncio
import json
from types import SimpleNamespace

import pytest

from corki.config import MCPServerSettings
from corki.mcp.client import StdioMCPClient, _finish_stdio_eof, _read_stdio
from corki.mcp.inbound import InboundService
from corki.mcp.json_rpc import MCPProtocolError


def reader_with_requests(*methods):
    reader = asyncio.StreamReader()
    for identity, method in enumerate(methods):
        reader.feed_data(
            json.dumps({"jsonrpc": "2.0", "id": identity, "method": method}).encode() + b"\n"
        )
    reader.feed_eof()
    return reader


@pytest.mark.parametrize("notification", [False, True])
def test_eof_drain_does_not_send_new_outbound_work(notification):
    async def scenario():
        sending, release = asyncio.Event(), asyncio.Event()
        outbound = []

        class Pipe:
            def write(self, data):
                outbound.append(data)

            async def drain(self):
                pass

        async def reply(message):
            sending.set()
            await release.wait()

        client = StdioMCPClient(
            MCPServerSettings("fixture", "stdio", command="unused", timeout_seconds=0.03)
        )
        client._process = SimpleNamespace(stdin=Pipe(), returncode=None)
        client._inbound = InboundService(client._pending, reply)
        task = asyncio.create_task(
            _read_stdio(reader_with_requests("ping"), client._pending, inbound=client._inbound)
        )
        client._reader_task = task
        try:
            await asyncio.wait_for(sending.wait(), 0.3)
            with pytest.raises(MCPProtocolError, match="closed"):
                if notification:
                    await client.notify("notifications/custom", {})
                else:
                    await client.call_tool("side-effect", {})
            assert client.is_closed and outbound == [] and not client._pending
        finally:
            release.set()
            await task

    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["exit", "kill", "cancel"])
def test_eof_transport_close_owns_child_reaping(monkeypatch, outcome):
    async def scenario():
        waiting, release = asyncio.Event(), asyncio.Event()
        events, budgets = [], []
        original = asyncio.wait_for

        async def wait_for(awaitable, timeout):
            if timeout == 3:
                budgets.append(timeout)
                timeout = 0.02 if outcome == "kill" else timeout
            return await original(awaitable, timeout)

        class Pipe:
            def close(self):
                events.append("stdin closed")

        class Transport:
            def close(self):
                events.append("transport closed")

        class Process:
            stdin = Pipe()
            _transport = Transport()

            async def wait(self):
                events.append("wait")
                waiting.set()
                await release.wait()
                events.append("reaped")
                return 0

            def kill(self):
                events.append("kill")
                release.set()

        monkeypatch.setattr(asyncio, "wait_for", wait_for)
        task = asyncio.create_task(_finish_stdio_eof(Process()))
        try:
            await original(waiting.wait(), 0.3)
            assert events[:2] == ["stdin closed", "wait"]
            if outcome == "cancel":
                for _ in range(3):
                    task.cancel()
                    await asyncio.sleep(0)
                assert not task.done() and "transport closed" not in events
            if outcome != "kill":
                release.set()
            if outcome == "cancel":
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                await task
            assert budgets == [3]
            assert events[-2:] == ["reaped", "transport closed"]
            assert events.count("kill") == int(outcome == "kill")
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("method", ["ping", "roots/list", "sampling/createMessage", "custom"])
def test_stdio_eof_flushes_buffered_reverse_rpc(method):
    async def scenario():
        replies = []

        async def send(message):
            replies.append(message)

        pending = {10: asyncio.get_running_loop().create_future()}
        inbound = InboundService(pending, send)
        await _read_stdio(reader_with_requests(method), pending, inbound=inbound)
        with pytest.raises(MCPProtocolError, match="closed its output"):
            await pending[10]
        expected = (
            {"result": {}}
            if method == "ping"
            else {"result": {"roots": []}}
            if method == "roots/list"
            else {"error": {"code": -32601, "message": method}}
        )
        assert replies == [{"jsonrpc": "2.0", "id": 0, **expected}]
        assert not inbound._tasks

    asyncio.run(scenario())


def test_stdio_eof_uses_five_second_budget_then_joins_cleanup(monkeypatch, caplog):
    async def scenario():
        cleaning, release = asyncio.Event(), asyncio.Event()
        calls, closed, budgets = [], [], []

        async def send(message):
            calls.append(message)
            try:
                await asyncio.Future()
            finally:
                cleaning.set()
                await release.wait()
                closed.append(True)

        pending = {10: asyncio.get_running_loop().create_future()}
        inbound = InboundService(pending, send)
        original = inbound.aclose

        async def close(**kwargs):
            budgets.append(kwargs.get("grace_seconds"))
            await original(grace_seconds=0.02)

        monkeypatch.setattr(inbound, "aclose", close)
        reader = asyncio.create_task(
            _read_stdio(reader_with_requests("ping", "ping"), pending, inbound=inbound)
        )
        try:
            await asyncio.wait_for(cleaning.wait(), 0.5)
            assert budgets == [5.0] and len(calls) == 1
            assert not reader.done() and not pending[10].done()
            # Cancellation cannot detach the cleanup or leave the RPC future unresolved.
            for _ in range(3):
                reader.cancel()
                await asyncio.sleep(0)
            assert not reader.done() and not closed
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await reader
            with pytest.raises(MCPProtocolError):
                await pending[10]
            assert closed == [True] and not inbound._tasks
        finally:
            release.set()
            await asyncio.gather(reader, return_exceptions=True)
            if pending[10].done():
                pending[10].exception()

    asyncio.run(scenario())
    assert "timed out draining in-flight responses" in caplog.text


def test_explicit_close_interrupts_eof_grace_and_shares_cleanup():
    async def scenario():
        sending, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        closed = []

        async def send(message):
            sending.set()
            try:
                await asyncio.Future()
            finally:
                cleaning.set()
                await release.wait()
                closed.append(True)

        inbound = InboundService({}, send)
        reader = asyncio.create_task(_read_stdio(reader_with_requests("ping"), {}, inbound=inbound))
        closers = []
        try:
            await asyncio.wait_for(sending.wait(), 0.3)
            closers.append(asyncio.create_task(inbound.aclose()))
            await asyncio.wait_for(cleaning.wait(), 0.3)
            closers.append(asyncio.create_task(inbound.aclose()))
            for _ in range(3):
                closers[0].cancel()
                await asyncio.sleep(0)
            assert not reader.done() and all(not c.done() for c in closers)
            assert not closed
            release.set()
            results = await asyncio.gather(reader, *closers, return_exceptions=True)
            assert results[0] is None and results[2] is None
            assert isinstance(results[1], asyncio.CancelledError)
            assert closed == [True] and not inbound._tasks
        finally:
            release.set()
            await inbound.aclose()
            await asyncio.gather(reader, *closers, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["internal", "io", "cancel"])
def test_reader_failure_and_cancellation_classify_eof_grace(monkeypatch, failure):
    async def scenario():
        reading = asyncio.Event()
        budgets = []
        reader = asyncio.StreamReader()

        async def read():
            reading.set()
            if failure == "cancel":
                await asyncio.Future()
            if failure == "io":
                raise OSError("read failed")
            raise RuntimeError("reader failed")

        async def send(message):
            raise AssertionError("no reply should be sent")

        inbound = InboundService({}, send)
        original = inbound.aclose

        async def close(**kwargs):
            budgets.append(kwargs.get("grace_seconds"))
            await original(**kwargs)

        monkeypatch.setattr(reader, "readline", read)
        monkeypatch.setattr(inbound, "aclose", close)
        task = asyncio.create_task(_read_stdio(reader, {}, inbound=inbound))
        await reading.wait()
        if failure == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await task
        assert budgets == [5.0 if failure == "io" else 0.0]

    asyncio.run(scenario())


def test_stdio_eof_waits_for_active_and_queued_replies_before_failing_pending():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        replies = []

        async def send(message):
            entered.set()
            await release.wait()
            replies.append(message)

        pending = {10: asyncio.get_running_loop().create_future()}
        inbound = InboundService(pending, send)
        task = asyncio.create_task(
            _read_stdio(reader_with_requests("ping", "ping"), pending, inbound=inbound)
        )
        try:
            await asyncio.wait_for(entered.wait(), 0.3)
            assert not task.done() and not pending[10].done()
            # Admission has stopped even though the existing sink is still usable.
            inbound.receive({"jsonrpc": "2.0", "id": "late", "method": "ping"})
            release.set()
            await task
            assert [r["id"] for r in replies] == [0, 1]
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            with pytest.raises(MCPProtocolError):
                await pending[10]
        assert not inbound._tasks

    asyncio.run(scenario())
