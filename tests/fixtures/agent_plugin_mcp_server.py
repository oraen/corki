"""Real MCP fixture reporting only explicitly named synthetic variables and effects."""

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
            "serverInfo": {"name": "agent-plugin-fixture", "version": "1"},
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
        }
    elif method == "tools/list":
        result = {
            "tools": [
                {
                    "name": "write",
                    "description": "needle plugin env",
                    "inputSchema": {"type": "object"},
                }
            ]
        }
    elif method == "tools/call":
        calls += 1
        report = {
            "cwd": os.getcwd(),
            "calls": calls,
            "env": {
                key: os.environ.get(key)
                for key in (
                    "PLUGIN_ROOT",
                    "PLUGIN_DATA",
                    "CORKI_PLUGIN_FIXTURE_TOKEN",
                    "CORKI_PLUGIN_FIXTURE_STATIC",
                )
            },
        }
        Path(sys.argv[1]).write_text(json.dumps(report))
        result = {"content": [{"type": "text", "text": json.dumps(report)}]}
    else:
        raise AssertionError(method)
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
