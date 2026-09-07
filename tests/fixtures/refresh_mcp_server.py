"""Deterministic stdio lifecycle fixture; only accesses paths supplied by its test."""

import json
import sys
import time
from pathlib import Path

version = sys.argv[1]
for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    if message["method"] == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}}
    elif message["method"] == "tools/list":
        result = {
            "tools": [{"name": "lookup", "description": version, "inputSchema": {"type": "object"}}]
        }
    elif message["method"] == "tools/call":
        args = message["params"]["arguments"]
        Path(args["entered"]).write_text(version)
        deadline = time.monotonic() + 5
        while not Path(args["release"]).exists():
            if time.monotonic() > deadline:
                raise RuntimeError("test did not release the call")
            time.sleep(0.01)
        result = {"content": [{"type": "text", "text": version}]}
    else:
        raise AssertionError(message)
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
