"""A scheduled occurrence uses the real runtime and the same persisted tools as the UI."""

import asyncio
import json
import os
from dataclasses import replace

import pytest
from test_thread_settings_update import settings

from corki.core.runtime import LangGraphRuntime
from corki.cron.locking import FileLock
from corki.cron.store import CronStore
from corki.cron.worker import supervise
from corki.models import ModelCompleted
from corki.protocol.events import TurnFailed
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools.registry import ToolRegistry


def test_scheduled_real_runtime_has_isolated_history_and_shared_management(tmp_path):
    store = CronStore(tmp_path)
    job = store.save(
        {"name": "note", "prompt": "Summarize the notes", "schedule": "every 1h", "timezone": "UTC"}
    )
    requests = []
    failures = []

    class Model:
        calls = 0

        async def stream(self, request):
            requests.append(request)
            self.calls += 1
            turn, step = request.items[-1].turn_id, new_step_id()
            if self.calls == 1:
                assert "cron_manage" in {t.name for t in request.tools}
                yield ModelCompleted(
                    (
                        ToolCallItem(
                            ToolCall("list-" + turn, "cron_manage", {"action": "list"}), turn, step
                        ),
                    )
                )
            else:
                result = next(i for i in reversed(request.items) if isinstance(i, ToolResultItem))
                assert result.content.startswith("{"), result.content
                assert json.loads(result.content)["jobs"][0]["id"] == job
                yield ModelCompleted((AssistantMessageItem("Scheduled result", turn, step),))

        async def aclose(self):
            pass

    async def factory(**kwargs):
        from corki.protocol.session_source import SessionSource

        assert kwargs["session_source"] == SessionSource.from_startup_arg("cron")
        runtime = await LangGraphRuntime.acreate(**kwargs, model=Model(), registry=ToolRegistry())
        original = runtime.stream

        async def stream(prompt):
            async for event in original(prompt):
                if isinstance(event, TurnFailed):
                    failures.append(event.error)
                yield event

        runtime.stream = stream
        return runtime

    def configured(path, **_):
        return replace(settings(path), goals_enabled=False)

    async def scenario():
        for _ in range(2):
            run = store.claim(job)
            store.start_run(run)
            await supervise(store, run, factory=factory, settings_factory=configured)
            record = store.run(run)
            assert record["status"] == "completed", (record["error"], failures)
            assert record["output"] == "Scheduled result"
        assert len({r["session_id"] for r in store.history(job)}) == 2
        # Initial requests cannot inherit the earlier occurrence's assistant response.
        for request in requests[::2]:
            assert not any(isinstance(i, AssistantMessageItem) for i in request.items)

    asyncio.run(scenario())


@pytest.mark.parametrize("timeout_seconds", [0, 1])
def test_detached_worker_finishes_after_launcher_exit(tmp_path, timeout_seconds):
    import subprocess
    import sys
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    entered = threading.Event()
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert request["model"] == "task-model"
            entered.set()
            release.wait(60 if os.name == "nt" else 10)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunk = {
                "choices": [
                    {"index": 0, "delta": {"content": "detached result"}, "finish_reason": "stop"}
                ]
            }
            self.wfile.write(("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    store = CronStore(tmp_path)
    config = {
        "default": {"provider": "fixture", "model": "fixture-model"},
        "providers": {
            "fixture": {
                "base_url": f"http://127.0.0.1:{server.server_port}/v1",
                "api_mode": "chat_completions",
                "api_key": "fixture-not-a-credential",
                "models": {
                    "fixture-model": {"context_window": 262144},
                    "task-model": {"context_window": 262144},
                },
            }
        },
        "features": {"goals": False},
        "skills": {"enabled": False},
    }
    config["providers"]["unused-default"] = {"models": {"unused-model": {}}}
    config["default"] = {"provider": "unused-default", "model": "unused-model"}
    (tmp_path / "settings.json").write_text(json.dumps(config))
    job = store.save(
        {
            "name": "Detached check",
            "provider": "fixture",
            "model": "task-model",
            "prompt": "Return a short summary",
            "timeout_seconds": timeout_seconds,
            "schedule": "every 1h",
            "timezone": "UTC",
        }
    )
    try:
        script = (
            "from pathlib import Path; import sys; "
            "from corki.cron.service import CronService; "
            "CronService(Path(sys.argv[1])).run_now(sys.argv[2])"
        )
        result = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path), job],
            capture_output=True,
            text=True,
            timeout=60 if os.name == "nt" else 10,
        )
        assert result.returncode == 0, result.stderr
        assert entered.wait(60 if os.name == "nt" else 10), store.history(job)
        # Launcher has exited, but a live worker lock prevents a replacement ticker retiring it.
        from corki.cron.service import CronService

        replacement = CronService(tmp_path)
        replacement.recover(now=time.time() + 100)
        assert store.history(job)[0]["status"] == "running"
        if not timeout_seconds:
            release.set()
        for _ in range(600 if os.name == "nt" else 100):
            row = store.history(job)[0]
            if row["status"] not in {"claimed", "running"}:
                break
            time.sleep(0.1)
        assert row["status"] == ("failed" if timeout_seconds else "completed"), row
        result = store.run(row["id"])
        if timeout_seconds:
            assert "超时时间" in result["error"]
        else:
            assert result["output"] == "detached result"
        assert len(store.history(job)) == 1
    finally:
        release.set()
        try:
            # A failed assertion must also cancel this isolated occurrence.
            # Closing the HTTP server alone does not stop a detached worker.
            current = next(item for item in store.list()["jobs"] if item["id"] == job)
            store.change(job, "stop", version=current["version"])
            deadline = time.monotonic() + 30
            while True:
                rows = store.history(job)
                released = True
                for row in rows:
                    lock = FileLock(store.root / "locks" / (row["id"] + ".lock"))
                    try:
                        released = lock.acquire() and released
                    finally:
                        lock.close()
                if released and all(row["status"] not in {"claimed", "running"} for row in rows):
                    break
                assert time.monotonic() < deadline, "detached fixture worker did not stop"
                time.sleep(0.05)
        finally:
            server.shutdown()
            server.server_close()
