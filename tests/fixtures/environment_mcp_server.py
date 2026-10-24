"""Only report fixture-named values and presence flags, never dump inherited secrets."""

import json
import os
import sys

# MCP stdio is UTF-8 even when Windows pipes use a legacy locale.
for stream in (sys.stdin, sys.stdout):
    stream.reconfigure(encoding="utf-8")

for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    method = message["method"]
    if method == "initialize":
        result = {
            "serverInfo": {"name": "fixture", "version": "1"},
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
        }
    elif method == "tools/list":
        result = {
            "tools": [
                {
                    "name": "lookup",
                    "description": "environment needle",
                    "inputSchema": {"type": "object"},
                }
            ]
        }
    elif method == "tools/call":
        result = {
            "content": [],
            "structuredContent": {
                "exported": os.environ.get("CORKI_MCP_ENV_EXPORT"),
                "overridden": os.environ.get("CORKI_MCP_ENV_OVERRIDE"),
                "unrequested_present": "CORKI_MCP_UNREQUESTED" in os.environ,
                "internal_present": any(k.lower() == "node_repl_auth_token" for k in os.environ),
                "pid": os.getpid(),
            },
        }
    else:
        raise AssertionError(method)
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
