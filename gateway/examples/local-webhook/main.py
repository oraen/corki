"""Minimal authenticated loopback connector; outbound replies can be polled locally."""

import hmac
import json
import sys
import threading
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

write_lock = threading.Lock()
replies = deque(maxlen=50)
server = None
secret = ""


def emit(value):
    with write_lock:
        print(json.dumps(value, ensure_ascii=False), flush=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def authorized(self):
        return not self.headers.get("Origin") and hmac.compare_digest(
            self.headers.get("Authorization", ""), "Bearer " + secret
        )

    def respond(self, code, value):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if not self.authorized():
            return self.respond(403, {"error": "Unauthorized"})
        try:
            self.connection.settimeout(5)
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 128000:
                raise ValueError()
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise ValueError()
            emit({"id": uuid.uuid4().hex, "method": "inbound", "params": value})
            # This is connector acceptance only, not Agent success.
            self.respond(202, {"pending": True})
        except (ValueError, OSError):
            self.respond(400, {"error": "Invalid message"})

    def do_GET(self):
        if not self.authorized():
            return self.respond(403, {"error": "Unauthorized"})
        self.respond(200, list(replies))


try:
    for line in sys.stdin:
        value = json.loads(line)
        method = value.get("method")
        try:
            if method == "initialize":
                params = value["params"]
                if params["protocol"] != 1:
                    raise ValueError()
                secret = params["config"]["token"]
                if not secret:
                    raise ValueError()
                server = ThreadingHTTPServer(
                    ("127.0.0.1", int(params["config"].get("port") or 0)), Handler
                )
                server.daemon_threads = True
                (Path(params["state_directory"]) / "listen.json").write_text(
                    json.dumps({"port": server.server_port}), encoding="utf-8"
                )
                threading.Thread(target=server.serve_forever, daemon=True).start()
                emit({"id": value["id"], "result": {"protocol": 1}})
                emit({"method": "account", "params": {"id": Path(params["state_directory"]).name}})
                emit({"method": "status", "params": {"state": "online"}})
            elif method == "send":
                replies.append(value["params"])
                emit({"id": value["id"], "result": {"sent": True}})
            elif method == "shutdown":
                emit({"id": value["id"], "result": {}})
                break
            elif method:
                emit({"id": value["id"], "error": "Unknown method"})
            else:
                # Keep delivery receipts inspectable by this local example's caller.
                replies.append({"admission": value})
        except (ValueError, KeyError, OSError):
            emit({"id": value.get("id"), "error": "Invalid configuration or message"})
finally:
    if server:
        server.shutdown()
        server.server_close()
