"""Typed tools/list admission happens before legacy catalog collection."""

import asyncio
import json
import sys

import httpx
import pytest

from corki.config import MCPServerSettings
from corki.mcp.client import HttpMCPClient, MCPProtocolError, StdioMCPClient
from corki.protocol.wire_numbers import dumps_wire

TOOL = '{"name":"read","inputSchema":{}}'


def catalog_case(name):
    if name == "sequence_tool":
        return '{"tools":[["read",null,null,{},null,null,null,null]]}'
    if name == "sequence_root":
        # ListPromptsResult precedes ListToolsResult and shares its six slots.
        # This unknown Tool field disqualifies Prompt without changing the Tool.
        return '[null,null,null,null,null,[{"name":"read","inputSchema":{},"arguments":false}]]'
    if name == "sequence_shadowed":
        return "[null,null,null,null,null,[" + TOOL + "]]"
    if name == "normalized":
        return (
            '{"tools":[{"name":"read","inputSchema":{},"title":null,"unknown":"PRIVATE",'
            '"annotations":{"readOnlyHint":true,"unknown":false},'
            '"icons":[["urn:x",null,null,{"dark":null}]]}]}'
        )
    if name == "schema_value":
        return (
            '{"tools":[{"name":"read","inputSchema":{"type":{"$serde_json::private::RawValue":'
            + json.dumps('"object"')
            + '}},"_meta":{"big":1e999}}]}'
        )
    if name == "unknown_duplicates":
        return '{"tools":[' + TOOL + '],"unknown":null,"unknown":true}'
    if name == "duplicate_tools":
        return '{"tools":null,"tools":[' + TOOL + "]}"
    if name == "duplicate_name":
        return '{"tools":[{"name":"PRIVATE","name":"read","inputSchema":{}}]}'
    if name == "bad_member":
        return '{"tools":[7,' + TOOL + "]}"
    if name == "missing_schema":
        return '{"tools":[{"name":"read"}]}'
    if name == "bad_title":
        return '{"tools":[{"name":"read","inputSchema":{},"title":false}]}'
    if name == "bad_icon":
        return (
            '{"tools":[{"name":"read","inputSchema":{},"icons":[{"src":"x","theme":"PRIVATE"}]}]}'
        )
    if name == "bad_sequence":
        return '{"tools":[["read",null,null,{}]]}'
    if name == "bad_meta_shadow":
        return (
            '{"tools":[{"name":"read","inputSchema":{},"_meta":'
            '{"x":{"$serde_json::private::Number":"PRIVATE"},"x":1}}]}'
        )
    if name == "bad_cursor":
        return '{"tools":[' + TOOL + '],"nextCursor":1}'
    if name in ("complete_wins", "complete_falls_back"):
        values = "[]" if name == "complete_wins" else "false"
        return '{"tools":[' + TOOL + '],"completion":{"values":' + values + "}}"
    if name in ("cursor", "empty_cursor", "large_cursor"):
        cursor = {"cursor": "second", "empty_cursor": "", "large_cursor": "x" * 65537}[name]
        return '{"tools":[' + TOOL + '],"nextCursor":' + json.dumps(cursor) + "}"
    raise AssertionError(name)


INVALID = {
    "sequence_shadowed",
    "duplicate_tools",
    "duplicate_name",
    "bad_member",
    "missing_schema",
    "bad_title",
    "bad_icon",
    "bad_sequence",
    "bad_meta_shadow",
    "bad_cursor",
    "complete_wins",
}
VALID = [
    "sequence_tool",
    "sequence_root",
    "normalized",
    "schema_value",
    "unknown_duplicates",
    "complete_falls_back",
    "cursor",
    "empty_cursor",
    "large_cursor",
]


@pytest.mark.parametrize("carrier", ["json", "sse", "stdio"])
@pytest.mark.parametrize("name", VALID + sorted(INVALID))
def test_tool_catalog_raw_types_and_legacy_single_page(tmp_path, carrier, name):
    async def scenario():
        raw = catalog_case(name)
        requests = []
        init = {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "serverInfo": {"name": "fixture", "version": "1"},
        }
        if carrier == "stdio":
            script = (
                "import json,sys\ncount=0\n"
                "for line in sys.stdin:\n"
                " p=json.loads(line)\n"
                " if 'id' not in p: continue\n"
                " if p['method']=='initialize': result=" + repr(json.dumps(init)) + "\n"
                " else:\n"
                "  count+=1\n  assert count==1\n  result=" + repr(raw) + "\n"
                ' print(\'{"jsonrpc":"2.0","id":\'+str(p[\'id\'])'
                "+',\"result\":'+result+'}',flush=True)\n"
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
                if packet["method"] == "notifications/initialized":
                    return httpx.Response(202)
                if packet["method"] == "initialize":
                    result = json.dumps(init)
                else:
                    assert packet["method"] == "tools/list"
                    requests.append(packet)
                    result = raw if len(requests) == 1 else '{"tools":[]}'
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
            await client.start()
            if name in INVALID:
                with pytest.raises(MCPProtocolError) as error:
                    await client.list_tools()
                assert "PRIVATE" not in str(error.value)
            else:
                result = await client.list_tools()
                expected = {"name": "read", "inputSchema": {}}
                if name == "normalized":
                    expected.update(
                        annotations={"readOnlyHint": True},
                        icons=[{"src": "urn:x", "theme": "dark"}],
                    )
                if name == "schema_value":
                    assert (
                        dumps_wire(result) == '[{"name":"read","inputSchema":{"type":"object"},'
                        '"_meta":{"big":1e+999}}]'
                    )
                else:
                    assert result == (expected,)
            if carrier != "stdio":
                assert len(requests) == 1
                assert "cursor" not in requests[0]["params"]
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("count", [1024, 1025, 2048, 2049])
def test_ordinary_catalog_limit_is_2048_not_remote_claimed_apps(count):
    async def scenario():
        def respond(request):
            packet = json.loads(request.content)
            if packet["method"] == "notifications/initialized":
                return httpx.Response(202)
            result = (
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "serverInfo": {"name": "codex_apps", "version": "1"},
                }
                if packet["method"] == "initialize"
                else {
                    "tools": [{"name": f"t{i}", "inputSchema": {}} for i in range(count)],
                    "_meta": {"connector_id": "codex_apps"},
                }
            )
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": packet["id"], "result": result}
            )

        client = HttpMCPClient(
            MCPServerSettings("codex_apps", "http", url="https://fixture.invalid"),
            transport=httpx.MockTransport(respond),
        )
        try:
            await client.start()
            if count > 2048:
                with pytest.raises(MCPProtocolError, match="2048"):
                    await client.list_tools()
            else:
                assert len(await client.list_tools()) == count
        finally:
            await client.aclose()

    asyncio.run(scenario())
