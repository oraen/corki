import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from corki.platforms.liveness import process_alive
from corki.protocol.ids import new_tool_call_id
from corki.protocol.tools import ToolCall
from corki.tools.base import ToolContext
from corki.tools.custom import CustomTool, register_custom_tools, validate_custom_tool
from corki.tools.executor import ToolExecutor
from corki.tools.registry import ToolRegistry


def definition(**changes):
    return {
        "name": "echo",
        "description": "Echo JSON",
        "transport": "process",
        "command": sys.executable,
        "args": ["-X", "utf8", "-c", "import sys;print(sys.stdin.read())"],
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        **changes,
    }


def execute(tool, cwd, arguments):
    registry = ToolRegistry()
    registry.register(tool)
    return asyncio.run(
        ToolExecutor(registry, output_char_budget=20000).execute(
            ToolCall(new_tool_call_id(), tool.spec.name, arguments), ToolContext(cwd)
        )
    )


def test_process_roundtrip_no_shell_and_schema_validation(tmp_path):
    marker = tmp_path / "must-not-exist"
    text = f"$(touch {marker}) `touch {marker}` 中文"
    tool = CustomTool(definition())
    result = execute(tool, tmp_path, {"text": text})
    assert not result.is_error
    assert json.loads(result.content) == {"text": text}
    assert not marker.exists()
    assert execute(tool, tmp_path, {"text": 4}).is_error
    assert execute(tool, tmp_path, {}).is_error


def test_http_real_post_and_errors(tmp_path):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, data, self.headers.get("X-Test")))
            self.send_response(200 if self.path == "/ok" else 302)
            self.send_header("Location", "/ok")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        data = definition(
            transport="http",
            url=f"http://127.0.0.1:{server.server_port}/ok",
            headers={"X-Test": "secret"},
        )
        result = execute(CustomTool(data), tmp_path, {"text": "hello"})
        assert not result.is_error, result
        assert json.loads(result.content) == {"text": "hello"}
        assert calls == [("/ok", {"text": "hello"}, "secret")]
        data["url"] = data["url"].replace("/ok", "/redirect")
        failed = execute(CustomTool(data), tmp_path, {"text": "hello"})
        assert failed.is_error
        assert "HTTP 请求失败：302" in failed.content
        assert len(calls) == 2  # no redirect replay/credential forwarding
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_directory_discovery_disabled_invalid_and_snapshot(tmp_path):
    root = tmp_path / "tools/custom"
    root.mkdir(parents=True)
    (root / "echo.json").write_text(json.dumps(definition()))
    (root / "off.json").write_text(json.dumps(definition(name="off", enabled=False)))
    (root / "bad.json").write_text("{")
    (root / "other.json").write_text(json.dumps(definition(name="different")))
    (root / "link.json").symlink_to(root / "echo.json")
    registry = ToolRegistry()
    register_custom_tools(registry, tmp_path)
    assert registry.get("custom__echo") is not None
    assert registry.get("custom__off") is None
    assert registry.get("custom__different") is None
    (root / "echo.json").unlink()
    assert registry.get("custom__echo") is not None
    fresh = ToolRegistry()
    register_custom_tools(fresh, tmp_path)
    assert fresh.get("custom__echo") is None


def test_timeout_output_limit_and_exit_errors(tmp_path):
    for script, timeout in [
        ("import time;time.sleep(10)", 0.1),
        ("print('x'*1100000)", 5),
        ("import sys;print('failure',file=sys.stderr);sys.exit(2)", 5),
    ]:
        result = execute(
            CustomTool(definition(args=["-c", script], timeout_seconds=timeout)),
            tmp_path,
            {"text": "ok"},
        )
        assert result.is_error


def test_cancel_terminates_process(tmp_path):
    pidfile = tmp_path / "pid"
    ready = tmp_path / "ready"
    script = (
        "import os,time;from pathlib import Path;"
        f"Path({str(pidfile)!r}).write_text(str(os.getpid()));"
        f"Path({str(ready)!r}).write_text('ready');time.sleep(120)"
    )

    async def scenario():
        tool = CustomTool(definition(args=["-c", script]))
        task = asyncio.create_task(
            tool.execute(
                ToolCall(new_tool_call_id(), tool.spec.name, {"text": "x"}), ToolContext(tmp_path)
            )
        )
        try:
            async with asyncio.timeout(60):
                while not ready.exists():
                    await asyncio.sleep(0.02)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not process_alive(int(pidfile.read_text()))
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_permission_denial_prevents_spawn(tmp_path, monkeypatch):
    async def deny(permissions, argv, cwd, **kwargs):
        assert argv[0] == sys.executable
        raise ValueError("policy denied")

    monkeypatch.setattr("corki.tools.custom.sandbox_command", deny)
    tool = CustomTool(definition())
    with pytest.raises(ValueError, match="policy denied"):
        asyncio.run(
            tool.execute(
                ToolCall(new_tool_call_id(), tool.spec.name, {"text": "ok"}),
                ToolContext(tmp_path, execution_permissions=object()),
            )
        )


@pytest.mark.parametrize(
    "changes",
    [
        dict(name="../bad"),
        dict(timeout_seconds=float("nan")),
        dict(enabled="yes"),
        dict(args="hello"),
        dict(parameters=[]),
        dict(env={"BAD=KEY": "x"}),
        dict(transport="http", url="file:///etc/passwd"),
        dict(headers={"X": "a\nb"}),
    ],
)
def test_definition_validation(changes):
    with pytest.raises(ValueError):
        validate_custom_tool(definition(**changes))


def test_runtime_discovers_and_executes_custom_tool(tmp_path):
    from corki.config import CorkiSettings
    from corki.core import LangGraphRuntime
    from corki.models import ModelCompleted
    from corki.protocol.items import ToolCallItem, ToolResultItem, new_step_id

    home = tmp_path / "home"
    folder = home / "tools/custom"
    folder.mkdir(parents=True)
    (folder / "echo.json").write_text(json.dumps(definition()))
    seen = []

    class Model:
        async def stream(self, request):
            seen.append(request)
            if len(seen) == 1:
                assert any(tool.name == "custom__echo" for tool in request.tools)
                yield ModelCompleted(
                    (
                        ToolCallItem(
                            ToolCall(new_tool_call_id(), "custom__echo", {"text": "runtime works"}),
                            request.items[-1].turn_id,
                            new_step_id(),
                        ),
                    )
                )
            else:
                results = [item for item in request.items if isinstance(item, ToolResultItem)]
                assert results and "runtime works" in str(results[-1])
                yield ModelCompleted(())

        async def aclose(self):
            pass

    async def scenario():
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path, skills_enabled=False, memories_enabled=False, execution_permissions=None
            ),
            home_path=home,
            database_path=tmp_path / "session.db",
            model=Model(),
        )
        try:
            async for _ in runtime.stream("echo test"):
                pass
            assert len(seen) == 2
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_http_get_encodes_arguments_and_preserves_query(tmp_path):
    from urllib.parse import parse_qs, urlsplit

    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            query = parse_qs(urlsplit(self.path).query)
            seen.append(query)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(query).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        tool = CustomTool(
            definition(
                transport="http",
                method="GET",
                url=f"http://127.0.0.1:{server.server_port}/search?limit=3",
            )
        )
        result = execute(tool, tmp_path, {"text": "中文 & limit=99 # $(echo hello)"})
        assert not result.is_error, result
        assert seen == [{"limit": ["3"], "text": ["中文 & limit=99 # $(echo hello)"]}]
        assert json.loads(result.content) == seen[0]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    with pytest.raises(ValueError, match="GET 或 POST"):
        validate_custom_tool(
            definition(transport="http", method="DELETE", url="https://example.com")
        )
