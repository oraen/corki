"""Isolated desktop-host contract tests; never use the real Corki home."""

import asyncio
import json
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
from service import Service

from corki.protocol.events import (
    AssistantMessageCompleted,
    AssistantTextDelta,
    TurnCompleted,
    TurnStarted,
)
from corki.protocol.ids import ThreadId, TurnId, new_model_step_id
from corki.protocol.items import AssistantMessageItem, UserMessageItem
from corki.sessions.models import TurnRecord, TurnStatus


class FakeRuntime:
    calls = 0

    def __init__(self, **kw):
        from corki.storage.sqlite import SQLiteSessionRepository

        self.repository = SQLiteSessionRepository(kw["database_path"])
        self.thread = kw["thread_id"]
        self.settings = kw["settings"]
        self.cancelled = False
        self.closed = False
        self.background = ()

    async def stream(self, message, **kw):
        FakeRuntime.calls += 1
        turn = TurnId(str(uuid.uuid4()))
        await self.repository.create_thread(self.thread, self.settings.working_directory)
        await self.repository.save_turn(TurnRecord(turn, self.thread, TurnStatus.RUNNING, message))
        await self.repository.append_items(self.thread, (UserMessageItem(message, turn),))
        yield TurnStarted(self.thread, turn)
        await asyncio.sleep(0.02)
        yield AssistantTextDelta(self.thread, turn, "Hello", item_id="answer")
        yield AssistantMessageCompleted(self.thread, turn, "Hello", item_id="answer")
        await self.repository.append_items(
            self.thread, (AssistantMessageItem("Hello", turn, new_model_step_id()),)
        )
        await self.repository.save_turn(
            TurnRecord(turn, self.thread, TurnStatus.COMPLETED, message, "Hello")
        )
        yield TurnCompleted(self.thread, turn, "Hello")

    def set_execution_approval_handler(self, h):
        pass

    def set_mcp_elicitation_handler(self, h):
        pass

    def defer_goal_continuation(self):
        pass

    async def cancel_active(self):
        self.cancelled = True

    async def aclose(self):
        self.closed = True
        await self.repository.close()

    async def list_background_terminals(self):
        return self.background

    async def update_thread_settings(self, **kw):
        pass

    async def load_display_items_page(self, limit=1):
        await self.repository.create_thread(self.thread, self.settings.working_directory)
        return await self.repository.load_display_items_page(self.thread, limit=limit)


def setup(home):
    (home / "settings.json").write_text(
        json.dumps(
            {
                "default": {"provider": "test", "model": "test-model"},
                "providers": {
                    "test": {
                        "base_url": "http://127.0.0.1:1",
                        "api_key": "test-only-key",
                        "models": {"test-model": {"context_window": 262144}},
                    }
                },
            }
        )
    )


def test_catalog_missing_and_invalid(tmp_path):
    async def check():
        async def send(v):
            pass

        s = Service(tmp_path, send)
        assert not (await s.catalog({}))["valid"]
        (tmp_path / "settings.json").write_text("{bad")
        assert not (await s.catalog({}))["valid"]
        await s.close()

    asyncio.run(check())


def test_run_dedup_history_and_replay(tmp_path):
    async def check():
        setup(tmp_path)
        events = []

        async def send(v):
            events.append(v)

        async def factory(**kw):
            return FakeRuntime(**kw)

        s = Service(tmp_path, send, factory)
        catalog = await s.catalog({})
        assert catalog["valid"], catalog
        assert "test-only-key" not in json.dumps(catalog)
        request = {
            "requestId": str(uuid.uuid4()),
            "threadId": str(uuid.uuid4()),
            "content": "hello",
            "provider": "test",
            "model": "test-model",
        }
        first = await s.submit(request)
        second = await s.submit(request)
        assert first["id"] == second["id"]
        await asyncio.gather(*s.tasks.values())
        assert (await s.request_read({"requestId": request["requestId"]}))["state"] == "completed"
        history = await s.read_thread({"threadId": request["threadId"]})
        assert [m["content"] for m in history["messages"]] == ["hello", "Hello"]
        assert all(m["createdAt"] for m in history["messages"])
        assert (await s.list_threads({}))["threads"][0]["title"] == "hello"
        assert (await s.replay({"instance": s.instance, "sequence": 0}))["events"]
        assert (await s.replay({"instance": "old", "sequence": 0}))["reset"]
        with pytest.raises(ValueError):
            await s.submit({**request, "content": "changed"})
        await s.rename({"threadId": request["threadId"], "title": "changed"})
        assert (await s.list_threads({}))["threads"][0]["title"] == "changed"
        await s.close()

    asyncio.run(check())


def test_real_runtime_history_and_writer_conflict(tmp_path):
    from dataclasses import replace

    from corki.core.runtime import LangGraphRuntime
    from corki.models import ModelCompleted
    from corki.protocol.items import new_step_id
    from corki.storage.thread_writer import ThreadWriterConflict, ThreadWriterLease
    from corki.tools.registry import ToolRegistry

    class Model:
        async def stream(self, request):
            turn = request.items[-1].turn_id
            yield ModelCompleted((AssistantMessageItem("实际 Runtime 回复", turn, new_step_id()),))

        async def aclose(self):
            pass

    async def factory(**kw):
        kw["settings"] = replace(
            kw["settings"], skills_enabled=False, memories_generate=False, memories_use=False
        )
        return await LangGraphRuntime.acreate(
            **kw, model=Model(), registry=ToolRegistry(), load_plugins=False
        )

    async def check():
        setup(tmp_path)
        events = []

        async def send(value):
            events.append(value)

        s = Service(tmp_path, send, factory)
        request = {
            "requestId": str(uuid.uuid4()),
            "threadId": str(uuid.uuid4()),
            "content": "你好",
            "provider": "test",
            "model": "test-model",
        }
        # A CLI uses this same canonical lease. Desktop must not create a
        # receipt or invoke the model while another host owns the session.
        external = ThreadWriterLease(s.database, ThreadId(request["threadId"]))
        await external.acquire()
        try:
            with pytest.raises(ThreadWriterConflict):
                await s.submit(request)
            assert not s.tasks
            assert await s.request_read({"requestId": request["requestId"]}) is None
        finally:
            await external.aclose()
        await s.submit(request)
        await asyncio.gather(*s.tasks.values())
        receipt = await s.request_read({"requestId": request["requestId"]})
        assert receipt["state"] == "completed", receipt
        history = await s.read_thread({"threadId": request["threadId"]})
        assert any(m["content"] == "实际 Runtime 回复" for m in history["messages"])
        lease = ThreadWriterLease(s.database, ThreadId(request["threadId"]))
        with pytest.raises(ThreadWriterConflict):
            await lease.acquire()
        await s.close()
        await lease.acquire()
        await lease.aclose()

    asyncio.run(check())


def test_stdio_real_model_stream_and_shutdown(tmp_path):
    import subprocess
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from queue import Queue

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert data["model"] == "test-model"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for value in ("桌面协议", "完整回复"):
                chunk = {
                    "choices": [{"index": 0, "delta": {"content": value}, "finish_reason": None}]
                }
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                self.wfile.flush()
            self.wfile.write(
                b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
                b"data: [DONE]\n\n"
            )

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    setup(tmp_path)
    config = json.loads((tmp_path / "settings.json").read_text())
    config["providers"]["test"]["base_url"] = f"http://127.0.0.1:{server.server_port}/v1"
    config["providers"]["test"]["api_mode"] = "chat_completions"
    config["features"] = {"goals": False}
    config["skills"] = {"enabled": False}
    (tmp_path / "settings.json").write_text(json.dumps(config))
    import os

    env = {**os.environ, "CORKI_HOME": str(tmp_path)}
    proc = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "bridge/main.py"),
            "--home",
            str(tmp_path),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    output = Queue()
    threading.Thread(
        target=lambda: [output.put(json.loads(line)) for line in proc.stdout], daemon=True
    ).start()

    def request(method, p, identity):
        proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": identity, "method": method, "params": p}) + "\n"
        )
        proc.stdin.flush()

    try:
        request("initialize", {"protocolVersion": 1}, "init")
        assert output.get(timeout=15)["result"]["protocolVersion"] == 1
        submission = {
            "requestId": str(uuid.uuid4()),
            "threadId": str(uuid.uuid4()),
            "content": "hello",
            "provider": "test",
            "model": "test-model",
        }
        request("turn.submit", submission, "submit")
        events = []
        while True:
            row = output.get(timeout=30)
            assert "error" not in row, row
            if row.get("method") == "event":
                events.append(row["params"])
                if row["params"]["type"] == "run.settled":
                    break
        assert not any(e["type"] in ("host.error", "TurnFailed") for e in events), events
        assert any(e["type"] == "AssistantTextDelta" for e in events)
        request("threads.read", {"threadId": submission["threadId"]}, "read")
        while True:
            row = output.get(timeout=10)
            if row.get("id") == "read":
                break
        assert any("桌面协议完整回复" in m["content"] for m in row["result"]["messages"]), row
        proc.stdin.close()
        assert proc.wait(timeout=20) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        server.shutdown()
        server.server_close()


def test_stop_real_runtime_keeps_partial_history(tmp_path):
    from dataclasses import replace

    from corki.core.runtime import LangGraphRuntime
    from corki.models.types import ModelTextDelta
    from corki.tools.registry import ToolRegistry

    async def scenario():
        setup(tmp_path)
        streaming = asyncio.Event()
        events = []

        class SlowModel:
            async def stream(self, request):
                yield ModelTextDelta("partial desktop reply")
                streaming.set()
                await asyncio.Event().wait()

            async def aclose(self):
                pass

        async def factory(**kw):
            kw["settings"] = replace(
                kw["settings"], skills_enabled=False, memories_generate=False, memories_use=False
            )
            return await LangGraphRuntime.acreate(
                **kw, model=SlowModel(), registry=ToolRegistry(), load_plugins=False
            )

        async def send(value):
            events.append(value)

        service = Service(tmp_path, send, factory)
        request = {
            "requestId": str(uuid.uuid4()),
            "threadId": str(uuid.uuid4()),
            "content": "wait",
            "provider": "test",
            "model": "test-model",
        }
        try:
            await service.submit(request)
            await asyncio.wait_for(streaming.wait(), 10)
            await service.stop({"threadId": request["threadId"]})
            await asyncio.wait_for(asyncio.gather(*service.tasks.values()), 10)
            assert service.state.request(request["requestId"])["state"] == "cancelled"
            assert any(e["params"]["type"] == "TurnCancelled" for e in events)
        finally:
            await service.close()

    asyncio.run(scenario())


def test_real_runtime_question_response_is_routed(tmp_path):
    from dataclasses import replace

    from corki.core.runtime import LangGraphRuntime
    from corki.models import ModelCompleted
    from corki.protocol.items import ToolCallItem, new_step_id
    from corki.protocol.tools import ToolCall

    class QuestionModel:
        calls = 0

        async def stream(self, request):
            self.calls += 1
            turn, step = request.items[-1].turn_id, new_step_id()
            if self.calls == 1:
                call = ToolCall(
                    "scope-question",
                    "request_user_input",
                    {
                        "questions": [
                            {
                                "id": "scope",
                                "header": "Scope",
                                "question": "Which scope?",
                                "options": [{"label": "Small", "description": "A small change."}],
                            }
                        ]
                    },
                )
                yield ModelCompleted((ToolCallItem(call, turn, step),))
            else:
                yield ModelCompleted((AssistantMessageItem("answer received", turn, step),))

        async def aclose(self):
            pass

    async def scenario():
        setup(tmp_path)
        question = asyncio.Event()

        async def send(value):
            if value.get("params", {}).get("type") == "UserInputRequested":
                question.set()

        async def factory(**kw):
            kw["settings"] = replace(
                kw["settings"],
                skills_enabled=False,
                memories_generate=False,
                memories_use=False,
                collaboration_mode="plan",
                tool_mode="direct",
            )
            return await LangGraphRuntime.acreate(**kw, model=QuestionModel(), load_plugins=False)

        service = Service(tmp_path, send, factory)
        request = {
            "requestId": str(uuid.uuid4()),
            "threadId": str(uuid.uuid4()),
            "content": "ask",
            "provider": "test",
            "model": "test-model",
        }
        try:
            await service.submit(request)
            await asyncio.wait_for(question.wait(), 10)
            pending = next(iter(service.pending.values()))
            with pytest.raises(ValueError):
                await service.respond({"threadId": "wrong", "requestId": pending["request_id"]})
            await service.respond(
                {
                    "threadId": request["threadId"],
                    "requestId": pending["request_id"],
                    "response": {"answers": {"scope": {"answers": ["Small"]}}},
                }
            )
            await asyncio.wait_for(asyncio.gather(*service.tasks.values()), 10)
            assert service.state.request(request["requestId"])["state"] == "completed"
            assert not service.pending
        finally:
            await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["accept", "decline", "server-cancel"])
def test_router_approval_response_and_server_cancellation(tmp_path, action):
    from corki.mcp.elicitation import ElicitationRouter

    class RoutedRuntime(FakeRuntime):
        def set_execution_approval_handler(self, handler):
            self.router = ElicitationRouter(handler)

        def respond_execution_approval(self, identity, response, **kwargs):
            self.router.respond("local-shell", identity, response, content=kwargs)

    async def check():
        setup(tmp_path)
        events = []

        async def send(value):
            events.append(value)

        async def factory(**kw):
            return RoutedRuntime(**kw)

        service = Service(tmp_path, send, factory)
        thread = str(uuid.uuid4())
        try:
            await service.submit(
                {
                    "requestId": str(uuid.uuid4()),
                    "threadId": thread,
                    "content": "fixture",
                    "provider": "test",
                    "model": "test-model",
                }
            )
            await asyncio.gather(*service.tasks.values())
            runtime = service.runtimes[thread]
            request = asyncio.create_task(
                runtime.router.request_shell_approval(
                    {
                        "message": "Fixture approval; no command will execute",
                        "mode": "form",
                        "requestedSchema": {"type": "object", "properties": {}},
                    }
                )
            )
            async with asyncio.timeout(3):
                while not service.pending:
                    await asyncio.sleep(0)
            identity = next(iter(service.pending))
            if action == "server-cancel":
                request.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await request
            else:
                await service.respond(
                    {
                        "threadId": thread,
                        "requestId": identity,
                        "action": action,
                    }
                )
                assert (await request)["action"] == action
            assert not service.pending
            assert any(e["params"]["type"] == "interaction.resolved" for e in events)
        finally:
            await service.close()

    asyncio.run(check())


@pytest.mark.parametrize("action", ["accept", "decline"])
def test_real_runtime_execution_approval(tmp_path, action):
    from dataclasses import replace

    from corki.config.exec_policy import ExecPolicySource
    from corki.config.permissions import ExecutionPermissions
    from corki.core.runtime import LangGraphRuntime
    from corki.execution.bundled import bundled_compiler
    from corki.models import ModelCompleted
    from corki.protocol.items import ToolCallItem, ToolResultItem, new_step_id
    from corki.protocol.tools import ToolCall

    class ToolModel:
        calls = 0
        results = []

        async def stream(self, request):
            self.calls += 1
            turn, step = request.items[-1].turn_id, new_step_id()
            if self.calls == 1:
                yield ModelCompleted(
                    (
                        ToolCallItem(
                            ToolCall(
                                "desktop-exec",
                                "exec_command",
                                {
                                    "cmd": "printf DESKTOP_APPROVAL_FIXTURE",
                                    "login": False,
                                },
                            ),
                            turn,
                            step,
                        ),
                    )
                )
            else:
                self.results = [item for item in request.items if isinstance(item, ToolResultItem)]
                yield ModelCompleted((AssistantMessageItem("tool reviewed", turn, step),))

        async def aclose(self):
            pass

    async def check():
        setup(tmp_path)
        model = ToolModel()
        requested = asyncio.Event()

        async def send(message):
            if message.get("params", {}).get("type") == "approval":
                requested.set()

        async def factory(**kw):
            kw["settings"] = replace(
                kw["settings"],
                skills_enabled=False,
                memories_generate=False,
                memories_use=False,
                tool_mode="direct",
                execution_permissions=ExecutionPermissions(
                    bundled_compiler(),
                    tmp_path,
                    '{"type":"read-only"}',
                    approval_policy_json='"on-request"',
                    exec_policy_sources=(
                        ExecPolicySource(
                            str(tmp_path / "fixture.rules"),
                            'prefix_rule(pattern=["printf"], decision="prompt", '
                            'justification="Desktop fixture")',
                        ),
                    ),
                ),
            )
            return await LangGraphRuntime.acreate(**kw, model=model, load_plugins=False)

        service = Service(tmp_path, send, factory)
        thread = str(uuid.uuid4())
        try:
            await service.submit(
                {
                    "requestId": str(uuid.uuid4()),
                    "threadId": thread,
                    "content": "fixture approval",
                    "provider": "test",
                    "model": "test-model",
                }
            )
            await asyncio.wait_for(requested.wait(), 10)
            pending = next(iter(service.pending.values()))
            assert pending["kind"] == "shell_approval"
            assert "DESKTOP_APPROVAL_FIXTURE" in json.dumps(
                pending["params"]["_meta"]["tool_params"]
            )
            await service.respond(
                {
                    "threadId": thread,
                    "requestId": pending["request_id"],
                    "action": action,
                    "content": {"scope": "once"},
                }
            )
            await asyncio.wait_for(asyncio.gather(*service.tasks.values()), 20)
            assert model.results
            if action == "accept":
                assert not model.results[-1].is_error
                assert "DESKTOP_APPROVAL_FIXTURE" in model.results[-1].content
            else:
                assert model.results[-1].is_error
                assert "rejected" in model.results[-1].content
        finally:
            await service.close()

    asyncio.run(check())


def test_form_validation_and_execution_scope_use_owned_request(tmp_path):
    class Receiver:
        calls = []

        def respond_execution_approval(self, *args, **kw):
            self.calls.append(("execution", args, kw))

        def respond_mcp_elicitation(self, *args, **kw):
            self.calls.append(("mcp", args, kw))

        def defer_goal_continuation(self):
            pass

        async def cancel_active(self):
            pass

        async def aclose(self):
            pass

    async def check():
        async def send(message):
            pass

        service = Service(tmp_path, send)
        runtime = Receiver()
        service.runtimes["thread"] = runtime
        try:
            for scope in ("once", "session", "rule"):
                service.pending[scope] = {
                    "threadId": "thread",
                    "request_id": scope,
                    "server_name": "local-shell",
                    "kind": "shell_approval",
                    "params": {
                        "mode": "form",
                        "requestedSchema": {
                            "properties": {
                                "scope": {
                                    "type": "string",
                                    "enum": ["once", "session", "rule"],
                                    "default": "once",
                                },
                            }
                        },
                        "_meta": {"execpolicy_amendment": ["fixture-program"]},
                    },
                }
                await service.respond(
                    {
                        "threadId": "thread",
                        "requestId": scope,
                        "action": "accept",
                        "content": {"scope": scope},
                    }
                )
                kind, _, kwargs = runtime.calls[-1]
                assert kind == "execution"
                assert kwargs["remember"] is (scope == "session")
                assert kwargs["execpolicy_amendment"] == (
                    ["fixture-program"] if scope == "rule" else None
                )
            service.pending["form"] = {
                "threadId": "thread",
                "request_id": "form",
                "server_name": "local-shell",
                "kind": "server",
                "params": {
                    "mode": "form",
                    "requestedSchema": {
                        "properties": {
                            "n": {"type": "integer", "minimum": 1},
                            "enabled": {"type": "boolean", "default": True},
                        },
                        "required": ["n"],
                    },
                },
            }
            for content in ({}, {"n": 0}, {"n": 1.5}, {"n": True}, {"n": 2, "unknown": "value"}):
                with pytest.raises(ValueError):
                    await service.respond(
                        {
                            "threadId": "thread",
                            "requestId": "form",
                            "action": "accept",
                            "content": content,
                        }
                    )
                assert "form" in service.pending
            await service.respond(
                {"threadId": "thread", "requestId": "form", "action": "accept", "content": {"n": 2}}
            )
            kind, _, kwargs = runtime.calls[-1]
            assert kind == "mcp"  # The server name never grants local execution authority.
            assert kwargs["content"] == {"n": 2, "enabled": True}
            for action in ("accept", "decline", "cancel"):
                service.pending["url"] = {
                    "threadId": "thread",
                    "request_id": "url",
                    "kind": "server",
                    "server_name": "fixture",
                    "params": {
                        "mode": "url",
                        "url": "https://example.invalid/fixture",
                    },
                }
                await service.respond(
                    {
                        "threadId": "thread",
                        "requestId": "url",
                        "action": action,
                        "content": {"unexpected": "URL requests have no form fields"},
                    }
                )
                kind, args, kwargs = runtime.calls[-1]
                assert kind == "mcp" and args == ("fixture", "url", action)
                assert kwargs["content"] == {}
                assert "url" not in service.pending
        finally:
            await service.close()

    asyncio.run(check())


def test_idle_retirement_preserves_background_terminals_and_canonical_history(tmp_path):
    async def check():
        setup(tmp_path)
        hosts = []

        async def send(message):
            pass

        async def factory(**kw):
            runtime = FakeRuntime(**kw)
            hosts.append(runtime)
            if len(hosts) == 1:
                runtime.background = (object(),)
            return runtime

        service = Service(tmp_path, send, factory)
        service.runtime_cache_limit = 2
        threads = []
        try:
            for _ in range(4):
                thread = str(uuid.uuid4())
                threads.append(thread)
                await service.submit(
                    {
                        "requestId": str(uuid.uuid4()),
                        "threadId": thread,
                        "content": "cache fixture",
                        "provider": "test",
                        "model": "test-model",
                    }
                )
                await asyncio.gather(*service.tasks.values())
            assert len(service.runtimes) == 2
            assert not hosts[0].closed and not hosts[-1].closed
            assert hosts[1].closed and hosts[2].closed
            assert not service.live
            assert (await service.read_thread({"threadId": threads[1]}))["messages"][-1][
                "content"
            ] == "Hello"
            await service.submit(
                {
                    "requestId": str(uuid.uuid4()),
                    "threadId": threads[1],
                    "content": "restore evicted",
                    "provider": "test",
                    "model": "test-model",
                }
            )
            await asyncio.gather(*service.tasks.values())
            assert len(hosts) == 5
            assert not hosts[0].closed
            assert len(service.runtimes) == 2
        finally:
            await service.close()
        assert all(host.closed for host in hosts)

    asyncio.run(check())


def test_disconnected_admission_caller_reconciles_without_reexecution(tmp_path):
    async def check():
        setup(tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()
        created = []

        async def send(value):
            pass

        async def factory(**kw):
            entered.set()
            await release.wait()
            runtime = FakeRuntime(**kw)
            created.append(runtime)
            return runtime

        service = Service(tmp_path, send, factory)
        request = {
            "requestId": str(uuid.uuid4()),
            "content": "lost reply",
            "provider": "test",
            "model": "test-model",
        }
        before = FakeRuntime.calls
        try:
            caller = asyncio.create_task(service.submit(request))
            await entered.wait()
            assert (await service.request_read({"requestId": request["requestId"]}))[
                "state"
            ] == "preparing"
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert len(service.admissions) == 1
            # A retry of the same request waits for the original admission.
            retry = asyncio.create_task(service.submit(request))
            release.set()
            receipt = await retry
            await asyncio.gather(*service.tasks.values())
            await asyncio.sleep(0)
            assert not service.admissions
            assert FakeRuntime.calls == before + 1
            assert len(created) == 1
            assert receipt["thread_id"]
        finally:
            await service.close()
        # Simulate the renderer losing the acceptance reply, then restarting.
        # Durable receipt reconciliation must not invoke the factory or model.
        restored = Service(tmp_path, send, factory)
        try:
            result = await restored.submit(request)
            assert result["thread_id"] == receipt["thread_id"]
            assert result["state"] == "completed"
            assert FakeRuntime.calls == before + 1
            assert not restored.tasks and not restored.runtimes
        finally:
            await restored.close()

    asyncio.run(check())


@pytest.mark.parametrize("stage", ["factory", "prepare"])
def test_close_cancels_unaccepted_admission_and_releases_runtime(tmp_path, stage):
    async def check():
        setup(tmp_path)
        entered, cancelled = asyncio.Event(), asyncio.Event()
        created = []

        async def wait_for_shutdown():
            entered.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        class PreparingRuntime(FakeRuntime):
            async def load_display_items_page(self, limit=1):
                await wait_for_shutdown()

        async def factory(**kw):
            if stage == "factory":
                await wait_for_shutdown()
            runtime = PreparingRuntime(**kw)
            created.append(runtime)
            return runtime

        async def send(value):
            pass

        service = Service(tmp_path, send, factory)
        request = {
            "requestId": str(uuid.uuid4()),
            "content": "not accepted",
            "provider": "test",
            "model": "test-model",
        }
        caller = asyncio.create_task(service.submit(request))
        await entered.wait()
        await asyncio.wait_for(service.close(), timeout=3)
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert cancelled.is_set()
        assert not service.admissions and not service.tasks
        assert service.state.request(request["requestId"]) is None
        assert all(runtime.closed for runtime in created)

    asyncio.run(check())


def test_live_thread_snapshot_keeps_long_unfinished_reply(tmp_path):
    async def check():
        setup(tmp_path)
        ready, release = asyncio.Event(), asyncio.Event()
        expected = "长回复开头🦀" + "完整内容" * 25000

        class StreamingRuntime(FakeRuntime):
            async def stream(self, message, **kw):
                turn = TurnId(str(uuid.uuid4()))
                await self.repository.append_items(self.thread, (UserMessageItem(message, turn),))
                yield TurnStarted(self.thread, turn)
                for offset in range(0, len(expected), 1000):
                    yield AssistantTextDelta(
                        self.thread, turn, expected[offset : offset + 1000], item_id="long"
                    )
                ready.set()
                await release.wait()
                yield AssistantMessageCompleted(self.thread, turn, expected, item_id="long")
                yield TurnCompleted(self.thread, turn, expected)

        async def send(value):
            pass

        async def factory(**kw):
            return StreamingRuntime(**kw)

        service = Service(tmp_path, send, factory)
        try:
            receipt = await service.submit(
                {
                    "requestId": str(uuid.uuid4()),
                    "content": "long",
                    "provider": "test",
                    "model": "test-model",
                }
            )
            await asyncio.wait_for(ready.wait(), 5)
            snapshot = await service.read_thread({"threadId": receipt["thread_id"]})
            assert snapshot["running"]
            deltas = [event for event in snapshot["live"] if event["type"] == "AssistantTextDelta"]
            assert len(deltas) == 1
            assert str(deltas[0]["data"]["delta"]) == expected
            release.set()
            await asyncio.gather(*service.tasks.values())
        finally:
            release.set()
            await service.close()

    asyncio.run(check())


@pytest.mark.parametrize("stop_continuation", [False, True])
def test_desktop_goal_continuation_and_stop(tmp_path, stop_continuation):
    """The desktop owns one run spanning all of the core's goal turns."""
    from dataclasses import replace

    from corki.core.runtime import LangGraphRuntime
    from corki.models import ModelCompleted
    from corki.protocol.items import ToolCallItem, new_step_id
    from corki.protocol.tools import ToolCall
    from corki.tools.registry import ToolRegistry

    async def check():
        setup(tmp_path)
        entered = asyncio.Event()
        events = []
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 2:
                    entered.set()
                    if stop_continuation:
                        await asyncio.Future()
                    item = ToolCallItem(
                        ToolCall("finish", "update_goal", {"status": "complete"}), turn, step
                    )
                else:
                    item = AssistantMessageItem(
                        "first part" if len(requests) == 1 else "verified done", turn, step
                    )
                yield ModelCompleted((item,))

            async def aclose(self):
                pass

        async def factory(**kw):
            kw["settings"] = replace(
                kw["settings"], skills_enabled=False, memories_generate=False,
                memories_use=False, tool_search_mode="disabled", goals_enabled=True,
            )
            runtime = await LangGraphRuntime.acreate(
                **kw, model=Model(), registry=ToolRegistry(), load_plugins=False
            )
            await runtime.goal_command("create", objective="Finish both fixture parts")
            return runtime

        async def send(event):
            events.append(event)

        service = Service(tmp_path, send, factory)
        request = {
            "requestId": str(uuid.uuid4()), "threadId": str(uuid.uuid4()),
            "content": "Continue the fixture goal", "provider": "test", "model": "test-model",
        }
        try:
            await service.submit(request)
            task = service.tasks[request["threadId"]]
            waiter = asyncio.create_task(entered.wait())
            await asyncio.wait({waiter, task}, timeout=10, return_when=asyncio.FIRST_COMPLETED)
            if not entered.is_set():
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
            assert entered.is_set(), json.dumps(events, ensure_ascii=False)
            if stop_continuation:
                await service.stop({"threadId": request["threadId"]})
            await asyncio.wait_for(task, 10)
            runtime = service.runtimes[request["threadId"]]
            goal = (await runtime.goal_command())["goal"]
            receipt = await service.request_read({"requestId": request["requestId"]})
            emitted = [event["params"] for event in events if event.get("method") == "event"]
            starts = [event for event in emitted if event["type"] == "TurnStarted"]
            assert len(starts) == 2
            assert len({event["data"]["turn_id"] for event in starts}) == 2
            assert sum(event["type"] == "run.settled" for event in emitted) == 1
            assert receipt["turn_id"] == starts[-1]["data"]["turn_id"]
            assert receipt["state"] == ("cancelled" if stop_continuation else "completed")
            assert len(requests) == (2 if stop_continuation else 3)
            # cancel_active(interrupted) pauses the goal in the core itself.
            assert goal["status"] == ("paused" if stop_continuation else "complete")
            assert request["threadId"] not in service.tasks
            items = await service.repository.load_items(ThreadId(request["threadId"]))
            assert sum(isinstance(item, UserMessageItem) for item in items) == 1
        finally:
            await service.close()

    asyncio.run(check())


def test_real_runtime_tool_start_is_delivered_while_tool_is_blocked(tmp_path):
    from dataclasses import replace
    from corki.core.runtime import LangGraphRuntime
    from corki.models import ModelCompleted
    from corki.protocol.items import ToolCallItem, new_step_id
    from corki.protocol.tools import ToolCall, ToolSpec, ToolResult
    from corki.tools.registry import ToolRegistry

    async def check():
        setup(tmp_path)
        release, entered, delivered = asyncio.Event(), asyncio.Event(), asyncio.Event()
        events = []
        class SlowTool:
            spec = ToolSpec("slow_fixture", "Wait for the test gate", {"type": "object"})
            async def execute(self, call, context):
                entered.set()
                await release.wait()
                return ToolResult(call.id, call.name, "done")
        class Model:
            calls = 0
            async def stream(self, request):
                self.calls += 1
                turn, step = request.items[-1].turn_id, new_step_id()
                yield ModelCompleted((ToolCallItem(ToolCall("slow", "slow_fixture", {}), turn, step),) if self.calls == 1
                                     else (AssistantMessageItem("done", turn, step),))
            async def aclose(self):
                pass
        async def factory(**kw):
            kw["settings"] = replace(kw["settings"], skills_enabled=False, memories_generate=False,
                                     memories_use=False, tool_mode="direct", tool_search_mode="disabled")
            registry = ToolRegistry()
            registry.register(SlowTool())
            return await LangGraphRuntime.acreate(**kw, model=Model(), registry=registry, load_plugins=False)
        async def send(event):
            events.append(event)
            if event.get("params", {}).get("type") == "ToolCallStarted":
                delivered.set()
        service = Service(tmp_path, send, factory)
        thread = str(uuid.uuid4())
        try:
            await service.submit(dict(requestId=str(uuid.uuid4()), threadId=thread, content="test",
                                      provider="test", model="test-model"))
            await asyncio.wait_for(entered.wait(), 10)
            await asyncio.wait_for(delivered.wait(), 2)
            assert not any(e.get("params", {}).get("type") == "ToolCallCompleted" for e in events)
            snapshot = await service.read_thread({"threadId": thread})
            calls = [m for m in snapshot["messages"] if m.get("callId") == "slow" and m["role"] == "tool"]
            assert calls and calls[0]["streaming"] is True
            release.set()
            await asyncio.wait_for(service.tasks[thread], 10)
            assert any(e.get("params", {}).get("type") == "ToolCallCompleted" for e in events)
        finally:
            release.set()
            await service.close()
    asyncio.run(check())


def test_desktop_runtime_discovers_shared_skills_from_user_home(tmp_path, monkeypatch):
    from corki.skills import SkillService
    user_home = tmp_path / "user-home"
    corki_home = tmp_path / "corki-home"
    corki_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user_home))
    shared = user_home / ".agents/skills/shared-fixture/SKILL.md"
    shared.parent.mkdir(parents=True)
    shared.write_text("---\nname: shared-fixture\ndescription: Test\n---\nGuide")
    excluded = user_home / ".codex/skills/excluded-fixture/SKILL.md"
    excluded.parent.mkdir(parents=True)
    excluded.write_text("---\nname: excluded-fixture\ndescription: Test\n---\nGuide")
    discovered = []

    async def check():
        setup(corki_home)
        async def send(event):
            pass
        async def factory(**kw):
            assert kw["compatibility_home"] == user_home
            service = SkillService(home=kw["home_path"], project_root=kw["settings"].working_directory,
                                   compatibility_home=kw["compatibility_home"], bundled_enabled=False)
            discovered.extend(s.name for s in service.snapshot(kw["settings"].working_directory).skills)
            return FakeRuntime(**kw)
        service = Service(corki_home, send, factory)
        try:
            await service.submit({"requestId": str(uuid.uuid4()), "threadId": str(uuid.uuid4()),
                                  "content": "hello", "provider": "test", "model": "test-model"})
            await asyncio.gather(*service.tasks.values())
            assert "shared-fixture" in discovered
            assert "excluded-fixture" not in discovered
        finally:
            await service.close()
    asyncio.run(check())


def test_display_pages_hide_compaction_copies_and_use_original_question(tmp_path):
    from corki.protocol.items import CompactionItem
    async def check():
        setup(tmp_path)
        async def send(event):
            pass
        service = Service(tmp_path, send)
        thread, turn = ThreadId(str(uuid.uuid4())), TurnId(str(uuid.uuid4()))
        question = UserMessageItem("fixture question", turn)
        progress = AssistantMessageItem("fixture progress", turn, new_model_step_id(), phase="commentary")
        retained = UserMessageItem(question.content, turn, retained_from_id=question.id)
        # The marker is outside the latest page. Its replacement span must still
        # be honored, and the context anchor must use the original question ID.
        replacements = [AssistantMessageItem("context copy", turn, new_model_step_id()) for _ in range(65)]
        answer = AssistantMessageItem("fixture final answer", turn, new_model_step_id(), phase="final_answer")
        try:
            await service.repository.create_thread(thread, tmp_path)
            await service.repository.save_turn(TurnRecord(turn, thread, TurnStatus.COMPLETED, question.content, answer.content))
            await service.repository.append_items(thread, (question, progress,
                CompactionItem("summary", progress.id, turn, replacement_item_count=len(replacements)),
                *replacements, retained, answer))
            page = await service.read_thread({"threadId": str(thread)})
            assert [m["id"] for m in page["messages"]] == [str(question.id), str(answer.id)]
            assert page["messages"][0]["contextOnly"]
            earlier = await service.read_thread({"threadId": str(thread), "before": page["next"]})
            assert [m["id"] for m in earlier["messages"]] == [str(question.id), str(progress.id)]
        finally:
            await service.close()
    asyncio.run(check())
