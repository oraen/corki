"""Deterministic local MCP server awaiting explicit host input before completion."""

import json
import sys


def send(value):
    print(json.dumps(value), flush=True)


def result_for(method):
    if method == "tools/list":
        return {
            "tools": [{"name": "read", "description": "needle", "inputSchema": {"type": "object"}}]
        }
    return {"content": [{"type": "text", "text": "host response received"}]}


pending = None
for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if method is None:
        assert message["id"] == "server-private-id"
        assert message["result"]["action"] in ("accept", "decline", "cancel")
        original, original_method = pending
        send({"jsonrpc": "2.0", "id": original, "result": result_for(original_method)})
        pending = None
    elif method == "initialize":
        send(
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "serverInfo": {"name": "fixture", "version": "1"},
                },
            }
        )
    elif method in ("tools/list", "tools/call"):
        if method == sys.argv[1]:
            pending = message["id"], method
            send(
                {
                    "jsonrpc": "2.0",
                    "id": "server-private-id",
                    "method": "elicitation/create",
                    "params": {
                        "message": "Choose a label",
                        "requestedSchema": json.loads(sys.argv[2])
                        if len(sys.argv) > 2
                        else {
                            "type": "object",
                            "properties": {"label": {"type": "string"}},
                        },
                        "_meta": {"progressToken": 7, "private": "HOST_ONLY_META"},
                    },
                }
            )
        else:
            send({"jsonrpc": "2.0", "id": message["id"], "result": result_for(method)})
