"""Echo actual request bytes as text; never coerce business numbers to floats."""

import json
import sys


def reply(raw):
    message = json.loads(raw, parse_int=str, parse_float=str)
    method = message["method"]
    if "id" not in message:
        return None
    if method == "initialize":
        result = {
            "serverInfo": {"name": "numbers", "version": "1"},
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
        }
    elif method == "tools/list":
        result = {
            "tools": [
                {
                    "name": "read",
                    "description": "numeric wire echo",
                    "inputSchema": {"type": "object"},
                }
            ]
        }
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": raw.strip()}]}
    else:
        raise AssertionError(method)
    return {"jsonrpc": "2.0", "id": int(message["id"]), "result": result}


if __name__ == "__main__":
    for line in sys.stdin:
        result = reply(line)
        if result is not None:
            print(json.dumps(result, ensure_ascii=False), flush=True)
