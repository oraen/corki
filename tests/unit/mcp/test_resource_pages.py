"""Resource RPCs expose typed single pages, not an eager aggregate."""

import asyncio
import json

import httpx
import pytest

from corki.config import MCPServerSettings
from corki.mcp.client import HttpMCPClient, MCPProtocolError
from corki.protocol.wire_numbers import dumps_wire


@pytest.mark.parametrize("carrier", ["json", "sse"])
@pytest.mark.parametrize("kind", ["resources", "templates", "read"])
@pytest.mark.parametrize("variant", ["sequence", "projection", "duplicate", "bad_member", "shadow"])
def test_resource_rpc_preserves_raw_types_before_public_projection(carrier, kind, variant):
    async def scenario():
        field = {"resources": "resources", "templates": "resourceTemplates", "read": "contents"}[
            kind
        ]
        item = {"uri": "fixture:guide", "name": "guide"}
        if kind == "templates":
            item = {"uriTemplate": "fixture:{name}", "name": "guide"}
        elif kind == "read":
            item = {"uri": "fixture:guide", "text": "body"}
        expected = {field: [dict(item)]}
        raw = json.dumps({field: [item]})
        if variant == "sequence":
            entry = (
                item
                if kind == "read"
                else [*item.values(), *([None] * (7 if kind == "resources" else 6))]
            )
            raw = json.dumps(
                [None, None, None, [entry], None]
                if kind == "read"
                else [None, None, None, None, None, [entry]]
            )
        elif variant == "projection":
            item.update(unknown="PRIVATE", _meta={"v": {"$serde_json::private::RawValue": "1e999"}})
            raw = json.dumps(
                {field: [item], "ttlMs": -1, "cacheScope": {"private": None}, "unknown": "PRIVATE"}
            )
            expected.update(ttlMs=0, cacheScope="private")
        elif variant == "duplicate":
            raw = '{"' + field + '":null,"' + field + '":[' + json.dumps(item) + "]}"
        elif variant == "bad_member":
            raw = json.dumps({field: [item, 7]})
        elif variant == "shadow":
            raw = json.dumps({field: [item], "completion": {"values": []}})

        def respond(request):
            packet = json.loads(request.content)
            if packet["method"] == "notifications/initialized":
                return httpx.Response(202)
            result = (
                json.dumps(
                    {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": {"name": "fixture", "version": "1"},
                    }
                )
                if packet["method"] == "initialize"
                else raw
            )
            body = '{"jsonrpc":"2.0","id":' + str(packet["id"]) + ',"result":' + result + "}"
            return httpx.Response(
                200,
                headers={
                    "content-type": "application/json" if carrier == "json" else "text/event-stream"
                },
                content=body if carrier == "json" else "data: " + body + "\n\n",
            )

        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid"),
            transport=httpx.MockTransport(respond),
        )
        try:
            await client.start()
            invoke = (
                client.list_resources
                if kind == "resources"
                else client.list_resource_templates
                if kind == "templates"
                else lambda: client.read_resource("fixture:guide")
            )
            if variant in ("duplicate", "bad_member", "shadow"):
                with pytest.raises(MCPProtocolError):
                    await invoke()
            else:
                result = await invoke()
                if variant == "projection":
                    assert isinstance(result, dict)
                    entry = result[field][0]
                    assert dumps_wire(entry.pop("_meta")) == '{"v":1e+999}'
                    assert "PRIVATE" not in repr(result)
                assert result == expected
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["resources", "templates"])
@pytest.mark.parametrize("cursor", [None, "", "second"])
def test_resource_rpc_takes_one_page_without_aggregate_item_limit(kind, cursor):
    async def scenario():
        requests = []
        field = "resources" if kind == "resources" else "resourceTemplates"
        item = {"uri" if kind == "resources" else "uriTemplate": "fixture:x", "name": "x"}

        def respond(request):
            packet = json.loads(request.content)
            if packet["method"] == "notifications/initialized":
                return httpx.Response(202)
            if packet["method"] == "initialize":
                result = {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "serverInfo": {"name": "fixture", "version": "1"},
                }
            else:
                requests.append(packet)
                assert len(requests) == 1
                result = {field: [item] * 2049, "nextCursor": "next"}
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": packet["id"], "result": result}
            )

        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid"),
            transport=httpx.MockTransport(respond),
        )
        try:
            await client.start()
            invoke = (
                client.list_resources if kind == "resources" else client.list_resource_templates
            )
            result = await invoke(cursor)
            assert result == {field: [item] * 2049, "nextCursor": "next"}
            assert requests[0]["params"].get("cursor") == cursor
            assert ("cursor" in requests[0]["params"]) is (cursor is not None)
        finally:
            await client.aclose()

    asyncio.run(scenario())
