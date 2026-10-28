"""Actual independent gateway + connector process + Corki runtime + local SSE model."""

import base64
import http.client
import io
import json
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from corki_gateway.control import control, endpoint, status


def test_full_process_lifecycle_and_delivery(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CORKI_HOME", str(tmp_path))
    received = []

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for delta, finish in [
                ({"role": "assistant", "content": "Fixture reply"}, None),
                ({}, "stop"),
            ]:
                self.wfile.write(
                    (
                        "data: "
                        + json.dumps(
                            {
                                "id": "fixture",
                                "object": "chat.completion.chunk",
                                "model": "fixture",
                                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                            }
                        )
                        + "\n\n"
                    ).encode()
                )
            self.wfile.write(b"data: [DONE]\n\n")

    replies, pulls = [], []

    class StandardEndpoint(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/questions":
                pulls.append(body)
                value = (
                    {
                        "messages": [
                            {"id": "http-question", "text": "Standard HTTP question"},
                            {"id": "http-next", "text": "Next HTTP question"},
                        ],
                        "next_cursor": "one",
                    }
                    if len(pulls) == 1
                    else {"messages": []}
                )
            else:
                replies.append(body)
                value = {
                    "received": body["message_id"] == "http-next",
                    "message_id": body["message_id"],
                    "reply_id": body["reply_id"],
                }
            raw = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    standard = ThreadingHTTPServer(("127.0.0.1", 0), StandardEndpoint)
    threading.Thread(target=standard.serve_forever, daemon=True).start()
    model = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    threading.Thread(target=model.serve_forever, daemon=True).start()
    (tmp_path / "settings.json").write_text(
        json.dumps(
            {
                "providers": {
                    "fixture": {
                        "base_url": f"http://127.0.0.1:{model.server_port}/v1",
                        "api_mode": "chat_completions",
                        "api_key": "fixture-not-a-credential",
                        "models": {"fixture": {"context_window": 262144}},
                    }
                },
                "default": {"provider": "fixture", "model": "fixture"},
                "skills": {"enabled": False},
                "features": {"goals": False},
            }
        )
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "connector.json",
            json.dumps(
                {
                    "protocol": 1,
                    "id": "fixture",
                    "name": "Fixture",
                    "runtime": "python",
                    "entry": "main.py",
                }
            ),
        )
        z.write(Path(__file__).parent / "fixtures/connector.py", "main.py")
    assert not control(
        tmp_path, {"action": "install", "zip": base64.b64encode(buf.getvalue()).decode()}
    )["running"]

    def wait(predicate, timeout=30):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            result = predicate()
            if result:
                return result
            time.sleep(0.1)
        raise AssertionError("gateway condition timed out")

    def call_http(port, method, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            connection.request(
                method,
                "/rpc" if headers else "/",
                json.dumps(body) if body else None,
                headers or {},
            )
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    try:
        assert control(tmp_path, {"action": "power", "enabled": True})["running"]
        first = endpoint(tmp_path)
        # No anonymous or cross-origin management.
        assert (
            call_http(
                first["port"], "POST", {"action": "status"}, {"Authorization": "Bearer wrong"}
            )[0]
            == 403
        )
        assert (
            call_http(
                first["port"],
                "POST",
                {"action": "status"},
                {"Authorization": "Bearer " + first["token"], "Origin": "https://example.invalid"},
            )[0]
            == 403
        )
        assert control(tmp_path, {"action": "power", "enabled": True})["port"] == first["port"]
        control(tmp_path, {"action": "channel", "id": "fixture", "enabled": True})
        portfile = tmp_path / "gateway/accounts/fixture/fixture-port"
        wait(portfile.exists)
        port = int(portfile.read_text())
        message = {
            "id": "one",
            "sender": "fixture-person",
            "conversation": "fixture-person",
            "kind": "direct",
            "text": "Hello fixture",
        }
        assert call_http(port, "POST", message)[0] == 202
        wait(lambda: any(r["status"] == "completed" for r in status(tmp_path)["receipts"]))
        assert json.loads(call_http(port, "GET")[1])[0]["text"] == "Fixture reply"
        count = len(received)
        call_http(port, "POST", message)
        time.sleep(0.2)
        assert len(received) == count
        old_thread = status(tmp_path)["receipts"][0]["thread"]
        assert not control(tmp_path, {"action": "power", "enabled": False})["running"]
        assert endpoint(tmp_path) is None
        try:
            call_http(port, "GET")
        except OSError:
            pass
        else:
            raise AssertionError("connector orphan remained")
        assert control(tmp_path, {"action": "power", "enabled": True})["running"]
        wait(lambda: status(tmp_path)["statuses"].get("fixture", {}).get("state") == "online")
        port = int(portfile.read_text())
        call_http(port, "POST", {**message, "id": "two", "text": "Continue fixture"})
        wait(
            lambda: (
                len([r for r in status(tmp_path)["receipts"] if r["status"] == "completed"]) == 2
            )
        )
        assert {r["thread"] for r in status(tmp_path)["receipts"]} == {old_thread}
        assert "Hello fixture" in json.dumps(received[-1])
        # Two instances of the same installed connector, separate child processes
        # and credentials directories, explicitly bound to one core conversation.
        shared = control(
            tmp_path,
            {
                "action": "save-connection",
                "channel": "fixture",
                "name": "Account A",
                "values": {},
                "new_conversation": "Shared fixture",
            },
        )
        a = next(c for c in shared["connections"] if c["name"] == "Account A")
        shared = control(
            tmp_path,
            {
                "action": "save-connection",
                "channel": "fixture",
                "name": "Account B",
                "values": {},
                "conversation_id": a["conversation_id"],
            },
        )
        b = next(c for c in shared["connections"] if c["name"] == "Account B")
        ports = []
        for connection in (a, b):
            control(tmp_path, {"action": "channel", "id": connection["id"], "enabled": True})
            file = tmp_path / "gateway/accounts" / connection["id"] / "fixture-port"
            wait(file.exists)
            account_port = int(file.read_text())
            ports.append(account_port)
            call_http(account_port, "POST", {**message, "text": "Shared " + connection["name"]})
            wait(
                lambda connection=connection: any(
                    r["channel"] == connection["id"] and r["status"] == "completed"
                    for r in status(tmp_path)["receipts"]
                )
            )
        assert len(set(ports)) == 2
        rows = [r for r in status(tmp_path)["receipts"] if r["channel"] in (a["id"], b["id"])]
        assert len(rows) == 2 and len({r["thread"] for r in rows}) == 2
        assert "Shared Account A" not in json.dumps(received[-1])
        control(tmp_path, {"action": "delete-connection", "id": a["id"]})
        assert not (tmp_path / "gateway/accounts" / a["id"]).exists()
        assert json.loads(call_http(ports[1], "GET")[1])[0]["text"] == "Fixture reply"

        created = control(
            tmp_path,
            {
                "action": "create-standard",
                "name": "HTTP process fixture",
                "pull_url": f"http://127.0.0.1:{standard.server_port}/questions",
                "reply_url": f"http://127.0.0.1:{standard.server_port}/replies",
            },
        )
        http_account = next(
            c for c in created["connections"] if c["name"] == "HTTP process fixture"
        )
        assert not http_account["enabled"]
        before_count = len(received)
        control(tmp_path, {"action": "channel", "id": http_account["id"], "enabled": True})
        wait(lambda: len(replies) == 5)
        assert all(r == replies[0] for r in replies[:4]) and replies[4]["text"] == "Fixture reply"
        assert len(received) == before_count + 2
        view = control(tmp_path, {"action": "conversation", "id": http_account["id"]})
        assert any(m["content"] == "Standard HTTP question" for m in view["messages"])
        answers = [m for m in view["messages"] if m["role"] == "assistant"]
        assert len(answers) == 2
        assert [m["delivery_failed"] for m in answers] == [True, False]
        control(tmp_path, {"action": "channel", "id": http_account["id"], "enabled": False})

    finally:
        control(tmp_path, {"action": "power", "enabled": False})
        standard.shutdown()
        standard.server_close()
        model.shutdown()
        model.server_close()
