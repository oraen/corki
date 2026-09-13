"""The startup boundary must retain raw typed fields through result selection."""

import asyncio
import json
import sys

import httpx
import pytest

from corki.config import MCPServerSettings
from corki.mcp.client import HttpMCPClient, MCPProtocolError, StdioMCPClient

BASE = (
    '{"protocolVersion":"2025-06-18","capabilities":{},'
    '"serverInfo":{"name":"fixture","version":"1"},"instructions":"find needle"}'
)


def wire_case(name):
    if name == "info_sequence":
        return BASE.replace(
            '{"name":"fixture","version":"1"}', '["fixture",null,"1",null,null,null]'
        )
    if name == "root_sequence":
        return '["2025-06-18",{},["fixture",null,"1",null,null,null],"find needle",null]'
    if name == "capability_sequences":
        return BASE.replace(
            '"capabilities":{}', '"capabilities":[null,null,{},null,[true],[false,true],[false]]'
        )
    if name == "icon_enum":
        return BASE.replace(
            '"version":"1"', '"version":"1","icons":[{"src":"urn:x","theme":{"dark":null}}]'
        )
    if name == "icon_sequence":
        return BASE.replace(
            '"version":"1"', '"version":"1","icons":[["urn:x",null,null,{"light":null}]]'
        )
    if name in ("cache_value", "cache_shadow", "cache_invalid_shadow"):
        cache = '{"cacheable":{"$serde_json::private::RawValue":"false"}}'
        if name == "cache_shadow":
            cache = '{"cacheable":true,"cacheable":false}'
        if name == "cache_invalid_shadow":
            cache = '{"cacheable":{"$serde_json::private::Number":"PRIVATE"},"cacheable":false}'
        return BASE.replace(
            '"capabilities":{}',
            '"capabilities":{"experimental":{"codex/tool-catalog-cache":' + cache + "}}",
        )
    if name == "meta_literal_marker":
        return BASE[:-1] + ',"_meta":{"$serde_json::private::Number":"PRIVATE"}}'
    if name == "unknown_duplicates":
        return BASE[:-1] + ',"unknown":null,"unknown":true}'
    if name.startswith("duplicate_"):
        field = name.removeprefix("duplicate_")
        return BASE.replace('"' + field + '":', '"' + field + '":null,"' + field + '":', 1)
    if name == "bad_info_sequence":
        return BASE.replace('{"name":"fixture","version":"1"}', '["fixture",null,"1"]')
    if name == "bad_capability_sequence":
        return BASE.replace('"capabilities":{}', '"capabilities":{"tools":[]}')
    if name in ("discover_wins", "discover_falls_back"):
        ttl = "1" if name == "discover_wins" else "-1"
        return (
            BASE[:-1]
            + ',"resultType":"complete","supportedVersions":[],"ttlMs":'
            + ttl
            + ',"cacheScope":"public"}'
        )
    raise AssertionError(name)


VALID = [
    "info_sequence",
    "root_sequence",
    "capability_sequences",
    "icon_enum",
    "icon_sequence",
    "cache_value",
    "cache_shadow",
    "meta_literal_marker",
    "unknown_duplicates",
    "discover_falls_back",
]
INVALID = [
    "duplicate_protocolVersion",
    "duplicate_capabilities",
    "duplicate_serverInfo",
    "duplicate_name",
    "duplicate_version",
    "duplicate_instructions",
    "bad_info_sequence",
    "bad_capability_sequence",
    "discover_wins",
    "cache_invalid_shadow",
]


@pytest.mark.parametrize("carrier", ["json", "sse", "stdio"])
@pytest.mark.parametrize("name", VALID + INVALID)
def test_typed_startup_precedes_initialized_notification(tmp_path, carrier, name):
    async def scenario():
        raw, methods = wire_case(name), []
        if carrier == "stdio":
            script = (
                "import json,sys\n"
                "p=json.loads(sys.stdin.readline())\n"
                "assert p['method']=='initialize'\n"
                'print(\'{"jsonrpc":"2.0","id":\'+str(p[\'id\'])+\',"result":\'+'
                + repr(raw)
                + "+'}',flush=True)\n"
                "for line in sys.stdin:\n"
                " p=json.loads(line)\n"
                " if p['method']=='notifications/initialized': continue\n"
                " assert p['method']=='tools/call'\n"
                " print(json.dumps({'jsonrpc':'2.0','id':p['id'],"
                "'result':{'content':[]}}),flush=True)\n"
            )
            client = StdioMCPClient(
                MCPServerSettings(
                    "docs",
                    "stdio",
                    command=sys.executable,
                    args=("-u", "-c", script),
                    cwd=tmp_path,
                    timeout_seconds=1,
                )
            )
        else:

            def respond(request):
                packet = json.loads(request.content)
                methods.append(packet["method"])
                if packet["method"] == "notifications/initialized":
                    return httpx.Response(202)
                result = raw if packet["method"] == "initialize" else '{"content":[]}'
                body = '{"jsonrpc":"2.0","id":' + str(packet["id"]) + ',"result":' + result + "}"
                return httpx.Response(
                    200,
                    headers={
                        "content-type": "application/json"
                        if carrier == "json"
                        else "text/event-stream"
                    },
                    content=body if carrier == "json" else "data: " + body + "\n\n",
                )

            client = HttpMCPClient(
                MCPServerSettings("docs", "http", url="https://fixture.invalid", timeout_seconds=1),
                transport=httpx.MockTransport(respond),
            )
        try:
            if name in VALID:
                await client.start()
                assert client.server_instructions == "find needle"
                assert client.tool_catalog_cacheable is (not name.startswith("cache_"))
                assert (await client.call_tool("read", {}))["content"] == []
                if carrier != "stdio":
                    assert methods == ["initialize", "notifications/initialized", "tools/call"]
            else:
                with pytest.raises(MCPProtocolError) as error:
                    await client.start()
                assert "PRIVATE" not in str(error.value)
                assert not client._initialized and client.is_closed
                if carrier != "stdio":
                    assert methods == ["initialize"]
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("carrier", ["json", "sse"])
@pytest.mark.parametrize("name", ["duplicate_name", "discover_wins", "cache_invalid_shadow"])
def test_failed_typed_reinitialization_keeps_generation_and_never_replays_tool(carrier, name):
    async def scenario():
        initialized, calls, notifications = [], [], []

        def respond(request):
            if request.method == "DELETE":
                return httpx.Response(204)
            if request.method == "GET":
                return httpx.Response(405)
            packet = json.loads(request.content)
            method = packet["method"]
            if method == "notifications/initialized":
                notifications.append(packet)
                return httpx.Response(202)
            if method == "tools/call":
                calls.append(packet)
                return httpx.Response(404)
            assert method == "initialize"
            initialized.append(packet)
            raw = BASE if len(initialized) == 1 else wire_case(name)
            body = '{"jsonrpc":"2.0","id":' + str(packet["id"]) + ',"result":' + raw + "}"
            return httpx.Response(
                200,
                headers={
                    "mcp-session-id": str(len(initialized)),
                    "content-type": "application/json"
                    if carrier == "json"
                    else "text/event-stream",
                },
                content=body if carrier == "json" else "data: " + body + "\n\n",
            )

        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid", timeout_seconds=1),
            transport=httpx.MockTransport(respond),
        )
        try:
            await client.start()
            original = client._recovery.current
            with pytest.raises(MCPProtocolError, match="Invalid MCP initialize result"):
                await client.call_tool("read", {})
            assert client._recovery.current is original
            assert len(initialized) == 2 and len(calls) == len(notifications) == 1
        finally:
            await client.aclose()

    asyncio.run(scenario())
