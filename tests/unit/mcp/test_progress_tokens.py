import asyncio
import json
from copy import deepcopy

import httpx
import pytest

from corki.config import MCPServerSettings
from corki.mcp.client import HttpMCPClient, MCPProtocolError


def fixture_client(requests, *, statuses=()):
    failures = list(statuses)
    generation = 0

    async def handle(request):
        nonlocal generation
        if request.method == "GET":
            return httpx.Response(405)
        if request.method == "DELETE":
            return httpx.Response(204)
        message = json.loads(request.content)
        requests.append((message, request.headers.get("mcp-session-id")))
        method = message["method"]
        if method == "initialize":
            generation += 1
            return httpx.Response(
                200,
                headers={"mcp-session-id": str(generation)},
                json={
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": {"name": "fixture", "version": "1"},
                    },
                },
            )
        if "id" not in message:
            return httpx.Response(202)
        if failures:
            return httpx.Response(failures.pop(0))
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {"tools": []} if method == "tools/list" else {"content": []},
            },
        )

    return HttpMCPClient(
        MCPServerSettings("fixture", "http", url="https://fixture.invalid"),
        transport=httpx.MockTransport(handle),
    )


@pytest.mark.parametrize("metadata", [False, 1, "bad", []])
def test_invalid_metadata_is_rejected_before_sending_or_consuming_token(metadata):
    async def scenario():
        messages = []
        client = fixture_client(messages)
        try:
            await client.start()
            with pytest.raises(MCPProtocolError, match="_meta"):
                await client.request("tools/call", {"_meta": metadata})
            await client.list_tools()
            ordinary = [
                m
                for m, _ in messages
                if m["method"] not in ("initialize", "notifications/initialized")
            ]
            assert len(ordinary) == 1
            assert ordinary[0]["params"]["_meta"] == {"progressToken": 0}
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("start", [(1 << 63) - 1, 1 << 63, (1 << 64) - 1])
def test_progress_provider_has_locked_signed_i64_wire_semantics(start):
    async def scenario():
        messages = []
        client = fixture_client(messages)
        try:
            await client.start()
            client._next_progress_token = start
            await client.list_tools()
            await client.list_tools()
            tokens = [
                m["params"]["_meta"]["progressToken"]
                for m, _ in messages
                if m["method"] == "tools/list"
            ]
            values = [start, (start + 1) % (1 << 64)]
            assert tokens == [v if v < (1 << 63) else v - (1 << 64) for v in values]
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "method,params",
    [
        ("tools/list", {}),
        ("tools/call", {"name": "read", "arguments": {}}),
        ("resources/read", {"uri": "file:///fixture"}),
        ("prompts/get", {"name": "prompt"}),
        ("ping", {}),
        ("custom", {"_meta": None}),
    ],
)
def test_ordinary_rpc_has_independent_progress_token(method, params):
    async def scenario():
        messages = []
        client = fixture_client(messages)
        original = deepcopy(params)
        try:
            await client.start()
            await client.request(method, params)
            await client.notify("notifications/custom", {})
            await client.request(method, params)
            ordinary = [m for m, _ in messages if m["method"] == method]
            assert [m["params"].get("_meta") for m in ordinary] == [
                {"progressToken": 0},
                {"progressToken": 1},
            ]
            assert all(m["id"] != m["params"]["_meta"]["progressToken"] for m in ordinary)
            assert all(
                "_meta" not in m["params"]
                for m, _ in messages
                if m["method"] == "initialize" or "id" not in m
            )
            assert params == original
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_caller_token_is_replaced_without_mutating_other_metadata_or_arguments():
    async def scenario():
        messages = []
        client = fixture_client(messages)
        params = {
            "name": "read",
            "arguments": {"_meta": {"progressToken": "tool argument"}},
            "_meta": {"progressToken": "caller", "private": {"value": "preserve"}},
        }
        original = deepcopy(params)
        try:
            await client.start()
            await asyncio.gather(*(client.request("tools/call", params) for _ in range(8)))
            sent = [m["params"] for m, _ in messages if m["method"] == "tools/call"]
            assert sorted(p["_meta"]["progressToken"] for p in sent) == list(range(8))
            assert all(p["arguments"] == original["arguments"] for p in sent)
            assert all(p["_meta"]["private"] == {"value": "preserve"} for p in sent)
            assert params == original
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [404, 503])
def test_rpc_retry_uses_generation_local_new_progress_token(status):
    async def scenario():
        messages = []
        client = fixture_client(messages, statuses=[status])
        try:
            await client.start()
            assert await client.list_tools() == ()
            calls = [(m, g) for m, g in messages if m["method"] == "tools/list"]
            assert [m["params"].get("_meta") for m, _ in calls] == [
                {"progressToken": 0},
                {"progressToken": 0 if status == 404 else 1},
            ]
            assert [g for _, g in calls] == ["1", "2" if status == 404 else "1"]
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("matching", [False, True])
def test_progress_does_not_extend_default_rpc_timeout_or_replay(matching):
    async def scenario():
        packets, posts, closed = [], [], []

        class Stream(httpx.AsyncByteStream):
            def __init__(self, token):
                self.token = token

            async def __aiter__(self):
                while True:
                    packets.append(self.token)
                    message = {
                        "jsonrpc": "2.0",
                        "method": "notifications/progress",
                        "params": {"progressToken": self.token, "progress": len(packets)},
                    }
                    yield b"data: " + json.dumps(message).encode() + b"\n\n"
                    await asyncio.sleep(0.004)

            async def aclose(self):
                closed.append(True)

        def handle(request):
            message = json.loads(request.content)
            posts.append(message)
            token = message["params"]["_meta"]["progressToken"] if matching else "unrelated"
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=Stream(token)
            )

        client = HttpMCPClient(
            MCPServerSettings(
                "fixture", "http", url="https://fixture.invalid", timeout_seconds=0.06
            ),
            transport=httpx.MockTransport(handle),
        )
        task = asyncio.create_task(client.call_tool("read", {}))
        try:
            done, _ = await asyncio.wait({task}, timeout=0.3)
            assert done, "progress notifications must not keep this RPC alive"
            with pytest.raises(TimeoutError):
                await task
            assert len(packets) >= 2 and len(posts) == 1 and closed == [True]
        finally:
            await client.aclose()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
