"""Local server that holds tools/call until its reverse RPC receives a reply."""

import json
import os
import sys


def send(value):
    print(json.dumps(value), flush=True)


pending = None
for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if method is None:
        assert pending is not None
        original, expected = pending
        assert message == {"jsonrpc": "2.0", "id": original, **expected}, message
        send(
            {
                "jsonrpc": "2.0",
                "id": original,
                "result": {"content": [{"type": "text", "text": "reverse RPC completed"}]},
            }
        )
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
    elif method == "tools/list":
        send(
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {
                    "tools": [
                        {"name": "read", "description": "needle", "inputSchema": {"type": "object"}}
                    ]
                },
            }
        )
    elif method == "tools/call":
        name = sys.argv[1] if len(sys.argv) > 1 else message["params"]["name"]
        if name in {"disconnect", "partial_exit"}:
            if name == "partial_exit":
                sys.stdout.write('{"jsonrpc":"2.0","result":')
                sys.stdout.flush()
            os._exit(23)
        elif name == "progress":
            token = message["params"]["_meta"]["progressToken"]
            assert type(token) is int
            assert "_meta" not in message["params"]["arguments"]
            send(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/progress",
                    "params": {
                        "progressToken": token,
                        "progress": 1,
                        "message": "fixture progress",
                        "_meta": {"private": "PRIVATE_PROGRESS_META"},
                    },
                }
            )
            send(
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {"content": [{"type": "text", "text": "reverse RPC completed"}]},
                }
            )
        elif name == "eof":
            result = {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {"content": [{"type": "text", "text": "reverse RPC completed"}]},
            }
            ping = {"jsonrpc": "2.0", "id": "eof-ping", "method": "ping"}
            # One buffered write makes the result/ping/EOF ordering deterministic.
            sys.stdout.write(json.dumps(result) + "\n" + json.dumps(ping) + "\n")
            sys.stdout.flush()
            os.close(sys.stdout.fileno())
            reply = json.loads(sys.stdin.readline())
            assert reply == {"jsonrpc": "2.0", "id": "eof-ping", "result": {}}, reply
            assert sys.stdin.read() == "", "client did not close its write half"
            break
        elif name == "cancel":
            send(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/cancelled",
                    "params": {"requestId": message["id"], "reason": "server stopped"},
                }
            )
        else:
            result = (
                {"result": {}}
                if name == "ping"
                else {"result": {"roots": []}}
                if name == "roots/list"
                else {"error": {"code": -32601, "message": name}}
            )
            pending = message["id"], result
            send({"jsonrpc": "2.0", "id": message["id"], "method": name, "params": {}})
