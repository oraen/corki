"""Controlled MCP process tree: descendant ignores TERM and retains inherited pipes."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

if sys.argv[1] == "child":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    pid_path = Path(sys.argv[2])
    temporary = pid_path.with_suffix(".tmp")
    temporary.write_text(str(os.getpid()))
    temporary.replace(pid_path)
    while True:
        time.sleep(1)

pid_file, mode = Path(sys.argv[1]), sys.argv[2]
child = subprocess.Popen([sys.executable, __file__, "child", str(pid_file)])
while not pid_file.exists():
    time.sleep(0.005)
if mode == "no-init":
    while True:
        time.sleep(1)
for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    if message["method"] == "initialize":
        result = {
            "serverInfo": {"name": "fixture", "version": "1"},
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
        }
    elif message["method"] == "tools/list":
        result = {
            "tools": [
                {
                    "name": "lookup",
                    "description": "process tree needle",
                    "inputSchema": {"type": "object"},
                }
            ]
        }
    elif message["method"] == "tools/call":
        result = {"content": [{"type": "text", "text": str(child.pid)}]}
    else:
        raise AssertionError(message["method"])
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
    if mode == "exit" and message["method"] == "tools/list":
        os._exit(0)
