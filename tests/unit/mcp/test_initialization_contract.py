import asyncio
import json
import sys

import httpx
import pytest

from corki.config import MCPServerSettings
from corki.mcp.client import HttpMCPClient, MCPProtocolError, StdioMCPClient


def valid_result():
    return {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "serverInfo": {"name": "fixture", "version": "1"},
    }


INVALID = [
    (("protocolVersion",), ...),
    (("capabilities",), ...),
    (("serverInfo",), ...),
    (("protocolVersion",), 1),
    (("capabilities",), None),
    (("capabilities",), []),
    (("serverInfo",), None),
    (("serverInfo", "name"), ...),
    (("serverInfo", "version"), ...),
    (("serverInfo", "name"), 1),
    (("serverInfo", "version"), False),
    (("serverInfo", "title"), []),
    (("serverInfo", "description"), 7),
    (("serverInfo", "websiteUrl"), {}),
    (("serverInfo", "icons"), {}),
    (("serverInfo", "icons"), [{}]),
    (("serverInfo", "icons"), [{"src": 1}]),
    (("serverInfo", "icons"), [{"src": "x", "mimeType": []}]),
    (("serverInfo", "icons"), [{"src": "x", "sizes": [1]}]),
    (("serverInfo", "icons"), [{"src": "x", "theme": "auto"}]),
    (("capabilities", "tools"), True),
    (("capabilities", "tools"), {"listChanged": 1}),
    (("capabilities", "prompts"), {"listChanged": "false"}),
    (("capabilities", "resources"), {"subscribe": []}),
    (("capabilities", "resources"), {"listChanged": 0}),
    (("capabilities", "logging"), []),
    (("capabilities", "completions"), False),
    (("capabilities", "experimental"), {"vendor": None}),
    (("capabilities", "extensions"), {"vendor/x": True}),
    (("instructions",), ["PRIVATE"]),
    (("_meta",), "PRIVATE"),
]


@pytest.mark.parametrize("transport", ["json", "sse"])
@pytest.mark.parametrize("path,value", INVALID)
def test_invalid_initialize_fields_never_publish_or_notify(transport, path, value):
    async def scenario():
        result = valid_result()
        target = result
        for key in path[:-1]:
            target = target[key]
        if value is ...:
            del target[path[-1]]
        else:
            target[path[-1]] = value
        methods = []

        def handle(request):
            message = json.loads(request.content)
            methods.append(message["method"])
            if "id" not in message:
                return httpx.Response(202)
            packet = {"jsonrpc": "2.0", "id": message["id"], "result": result}
            if transport == "json":
                return httpx.Response(200, json=packet)
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text="data: " + json.dumps(packet) + "\n\n",
            )

        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid", timeout_seconds=0.2),
            transport=httpx.MockTransport(handle),
        )
        try:
            with pytest.raises(MCPProtocolError) as caught:
                await client.start()
            assert "PRIVATE" not in str(caught.value)
            assert methods == ["initialize"]
            assert not client._initialized and not client._recovery.ready
            assert client.is_closed
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("version", ["", "future-version", "2025-03-26"])
def test_source_optional_null_unknown_and_empty_strings_are_accepted(version):
    async def scenario():
        result = valid_result()
        result.update(protocolVersion=version, instructions=None, _meta=None, unknown=[1])
        result["serverInfo"] = {
            "name": "",
            "version": "",
            "title": None,
            "description": None,
            "websiteUrl": None,
            "icons": [{"src": "", "sizes": ["not-validated"], "mimeType": None, "theme": "dark"}],
        }
        result["capabilities"] = {
            "tools": {"listChanged": None, "unknown": 1},
            "resources": {"subscribe": False},
            "prompts": None,
            "logging": {},
            "completions": None,
            "experimental": {"vendor": {}},
            "extensions": {"vendor/x": {"enabled": True}},
        }
        methods = []

        def handle(request):
            packet = json.loads(request.content)
            methods.append(packet["method"])
            if "id" not in packet:
                assert request.headers["mcp-protocol-version"] == version
                return httpx.Response(202)
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": packet["id"], "result": result}
            )

        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid"),
            transport=httpx.MockTransport(handle),
        )
        try:
            await client.start()
            assert client._recovery.ready and methods == ["initialize", "notifications/initialized"]
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "kind", ["control", "malformed", "conflict", "error_then_response", "bad_version"]
)
def test_initialize_sse_has_distinct_first_response_rules(kind):
    async def scenario():
        methods = []
        good = {"jsonrpc": "2.0", "id": 1, "result": valid_result()}

        def frame(packet):
            return "data: " + json.dumps(packet) + "\n\n"

        prefix = {
            "control": "event: ping\n",
            "malformed": "data: {PRIVATE invalid json}\n\n",
            "conflict": frame(dict(good, id=99)),
            "error_then_response": frame(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "error": {"code": -1, "message": "ignored by init SSE transport"},
                }
            ),
            "bad_version": frame(dict(good, jsonrpc="1.0")),
        }[kind]

        def handle(request):
            message = json.loads(request.content)
            methods.append(message["method"])
            if "id" not in message:
                return httpx.Response(202)
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, text=prefix + frame(good)
            )

        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid", timeout_seconds=0.2),
            transport=httpx.MockTransport(handle),
        )
        try:
            if kind in ("control", "error_then_response"):
                await client.start()
                assert methods == ["initialize", "notifications/initialized"]
            else:
                with pytest.raises(MCPProtocolError) as caught:
                    await client.start()
                assert "PRIVATE" not in str(caught.value) and methods == ["initialize"]
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "kind", ["missing_info", "conflict", "malformed", "valid", "bom", "badshape"]
)
def test_real_stdio_initialization_contract(tmp_path, kind):
    async def scenario():
        result = valid_result()
        if kind == "missing_info":
            result.pop("serverInfo")
        packet = {"jsonrpc": "2.0", "id": 1, "result": result}
        prefix = "{PRIVATE invalid}\n" if kind == "malformed" else ""
        if kind == "conflict":
            prefix = json.dumps(dict(packet, id=99)) + "\n"
        if kind == "bom":
            prefix = "\ufeff"
        handshake_error = ""
        if kind == "badshape":
            handshake_error = (
                'print(\'{"jsonrpc":"2.0","id":true,"result":{}}\',flush=True)\n'
                "reply=json.loads(sys.stdin.readline())\n"
                "assert reply == {'jsonrpc':'2.0',"
                "'error':{'code':-32600,'message':'Invalid request'}}\n"
            )
        script = (
            "import json,sys,time\n"
            "request=json.loads(sys.stdin.readline())\n"
            + handshake_error
            + f"sys.stdout.write({prefix + json.dumps(packet) + chr(10)!r})\n"
            "sys.stdout.flush()\n"
            "for line in sys.stdin: pass\n"
        )
        client = StdioMCPClient(
            MCPServerSettings(
                "docs",
                "stdio",
                command=sys.executable,
                args=("-u", "-c", script),
                cwd=tmp_path,
                timeout_seconds=0.5,
            )
        )
        try:
            if kind in ("valid", "malformed", "bom", "badshape"):
                await client.start()
                assert client._initialized
            else:
                with pytest.raises(MCPProtocolError) as caught:
                    await client.start()
                assert "PRIVATE" not in str(caught.value) and client.is_closed
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "patch",
    [
        {"jsonrpc": None},
        {"jsonrpc": "1.0"},
        {"id": True},
        {"id": 1.0},
        {"id": None},
        {"id": "unrelated"},
        {"result": []},
    ],
)
def test_initialize_json_envelope_is_not_a_loose_mapping(patch):
    async def scenario():
        def handle(request):
            message = json.loads(request.content)
            if "id" not in message:
                return httpx.Response(202)
            packet = {"jsonrpc": "2.0", "id": message["id"], "result": valid_result()}
            packet.update(patch)
            return httpx.Response(200, json=packet)

        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid"),
            transport=httpx.MockTransport(handle),
        )
        try:
            with pytest.raises(MCPProtocolError):
                await client.start()
            assert not client._recovery.ready and client.is_closed
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_uncorrelated_idless_initialize_error_keeps_remote_error():
    async def scenario():
        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid"),
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "error": {"code": -1, "message": "server rejected initialize"},
                    },
                )
            ),
        )
        try:
            with pytest.raises(MCPProtocolError, match="server rejected initialize"):
                await client.start()
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [200, 202, 204])
def test_initialized_notification_rejects_sse_without_reading_body(status):
    async def scenario():
        closed = []

        class Unread(httpx.AsyncByteStream):
            async def __aiter__(self):
                pytest.fail("Initialized notification SSE body must not be read")
                yield b""

            async def aclose(self):
                closed.append(True)

        def handle(request):
            message = json.loads(request.content)
            if "id" in message:
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": message["id"], "result": valid_result()}
                )
            return httpx.Response(
                status, headers={"content-type": "text/event-stream"}, stream=Unread()
            )

        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid"),
            transport=httpx.MockTransport(handle),
        )
        try:
            if status == 200:
                with pytest.raises(MCPProtocolError):
                    await client.start()
                assert not client._recovery.ready
            else:
                await client.start()
                assert client._recovery.ready
            assert closed == [True]
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["initialize", "notifications/initialized"])
@pytest.mark.parametrize("kind", ["mime", "json"])
def test_handshake_rejects_untyped_http_payload(phase, kind):
    async def scenario():
        methods = []

        def handle(request):
            message = json.loads(request.content)
            methods.append(message["method"])
            good = {"jsonrpc": "2.0", "id": message.get("id", 1), "result": valid_result()}
            if message["method"] == phase:
                return httpx.Response(
                    200,
                    headers={
                        "content-type": "text/plain" if kind == "mime" else "application/json"
                    },
                    content=json.dumps(good).encode() if kind == "mime" else b"{PRIVATE broken}",
                )
            return httpx.Response(200, json=good)

        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid"),
            transport=httpx.MockTransport(handle),
        )
        try:
            with pytest.raises(MCPProtocolError) as caught:
                await client.start()
            assert "PRIVATE" not in str(caught.value)
            assert methods.count("initialize") == 1 and not client._recovery.ready
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_invalid_reinitialization_never_publishes_replacement_or_replays_tool():
    async def scenario():
        initializes, calls, notifications = [], [], []

        def handle(request):
            if request.method == "DELETE":
                return httpx.Response(204)
            message = json.loads(request.content)
            if message["method"] == "notifications/initialized":
                notifications.append(message)
                return httpx.Response(202)
            if message["method"] == "tools/call":
                calls.append(message)
                return httpx.Response(404)
            assert message["method"] == "initialize"
            initializes.append(message)
            result = valid_result()
            if len(initializes) == 2:
                result["serverInfo"] = {"name": "missing version"}
            return httpx.Response(
                200,
                headers={"mcp-session-id": str(len(initializes))},
                json={"jsonrpc": "2.0", "id": message["id"], "result": result},
            )

        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid"),
            transport=httpx.MockTransport(handle),
        )
        try:
            await client.start()
            original = client._recovery.current
            with pytest.raises(MCPProtocolError, match="Invalid MCP initialize result"):
                await client.call_tool("read", {})
            assert client._recovery.current is original
            assert len(initializes) == 2 and len(calls) == len(notifications) == 1
        finally:
            await client.aclose()

    asyncio.run(scenario())
