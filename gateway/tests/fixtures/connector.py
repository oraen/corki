"""Disposable loopback transport for full gateway integration tests."""

import json
import sys
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

output_lock = threading.Lock()
replies = deque(maxlen=50)
server = None


def emit(value):
    with output_lock:
        print(json.dumps(value), flush=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        emit({"id": value["id"], "method": "inbound", "params": value})
        self.send_response(202)
        self.end_headers()

    def do_GET(self):
        body = json.dumps(list(replies)).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


for line in sys.stdin:
    value = json.loads(line)
    if value.get("method") == "initialize":
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        directory = Path(value["params"]["state_directory"])
        (directory / "fixture-port").write_text(str(server.server_port))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        emit({"id": value["id"], "result": {"protocol": 1}})
        emit({"method": "status", "params": {"state": "online"}})
    elif value.get("method") == "send":
        replies.append(value["params"])
        emit({"id": value["id"], "result": {"sent": True}})
    elif value.get("method") == "shutdown":
        emit({"id": value["id"], "result": {}})
        break
