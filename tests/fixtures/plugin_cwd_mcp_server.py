"""A real stdio MCP process reporting only its fixture cwd and call counter."""

import json
import os
import sys
from pathlib import Path

calls = 0
for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    method = message["method"]
    if method == "initialize":
        result = {
            "serverInfo": {"name": "cwd-fixture", "version": "1"},
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
        }
    elif method == "tools/list":
        result = {
            "tools": [
                {"name": "write", "description": "needle cwd", "inputSchema": {"type": "object"}}
            ]
        }
    elif method == "tools/call":
        calls += 1
        Path(sys.argv[1]).write_text(json.dumps({"cwd": os.getcwd(), "calls": calls}))
        result = {
            "content": [{"type": "text", "text": json.dumps({"cwd": os.getcwd(), "calls": calls})}]
        }
    else:
        raise AssertionError(method)
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
