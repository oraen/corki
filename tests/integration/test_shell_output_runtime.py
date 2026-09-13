import asyncio
import json
import shlex
import sqlite3
import sys

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("poll", [False, True])
@pytest.mark.parametrize("requested", [None, 4, 0])
def test_shell_actual_process_runtime_budget_and_cold_history(tmp_path, nested, poll, requested):
    async def scenario():
        raw = "HEAD" + "字" * 20000 + "TAIL"
        script = (
            "import sys; "
            + (
                "import tty; tty.setraw(0); print('READY', flush=True); sys.stdin.readline(); "
                if poll
                else ""
            )
            + "sys.stdout.write('HEAD'+'字'*20000+'TAIL')"
        )
        command = shlex.join([sys.executable, "-c", script])
        args = {"cmd": command, "login": False, "tty": poll, "yield_time_ms": 50 if poll else 1000}
        if poll:
            args["max_output_tokens"] = 1
        elif requested is not None:
            args["max_output_tokens"] = requested
        requests = []
        target = "write_stdin" if poll else "exec_command"
        calls = 0

        class Model:
            async def stream(self, request):
                nonlocal calls
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    if nested:
                        code = f"const r = await tools.exec_command({json.dumps(args)}); "
                        if poll:
                            options = {"chars": "go\n", "yield_time_ms": 1000}
                            if requested is not None:
                                options["max_output_tokens"] = requested
                            code += (
                                "const s = await tools.write_stdin({session_id:r.session_id, "
                                + json.dumps(options)[1:]
                                + "); text(s.output.length);"
                            )
                        else:
                            code += "text(r.output.length);"
                        call = ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            raw_arguments=code,
                            input_kind="freeform",
                        )
                    else:
                        call = ToolCall(new_tool_call_id(), "exec_command", args)
                    calls += 1
                    yield ModelCompleted((ToolCallItem(call, turn, step),))
                elif poll and not nested and len(requests) == 2:
                    result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    session = result.content.split("Process running with session ID ")[
                        1
                    ].splitlines()[0]
                    options = {"session_id": session, "chars": "go\n", "yield_time_ms": 1000}
                    if requested is not None:
                        options["max_output_tokens"] = requested
                    calls += 1
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(new_tool_call_id(), "write_stdin", options), turn, step
                            ),
                        )
                    )
                else:
                    result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    if nested:
                        assert "Script completed" in result.content
                    else:
                        assert "Chunk ID:" in result.content
                        assert "Original token count: 15002" in result.content
                        unit = "chars" if requested is None else "tokens"
                        assert result.content.count(f"{unit} truncated") == 1
                        assert len(result.content.encode()) <= 2400
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        database = tmp_path / "shell.db"
        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            tool_search_mode="disabled",
            tool_output_token_limit=500,
            tool_mode="code_mode" if nested else "direct",
        )

        def create(thread=None):
            return LangGraphRuntime.create(
                settings=settings, database_path=database, model=Model(), thread_id=thread
            )

        runtime = create()
        thread = runtime.thread_id
        try:
            events = [e async for e in runtime.stream("execute")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            with sqlite3.connect(database) as db:
                (encoded,) = db.execute(
                    "SELECT result_json FROM tool_executions WHERE tool_name=?", (target,)
                ).fetchone()
                result = json.loads(encoded)
                public = result["code_mode_output"]["value"]
                assert public["original_token_count"] == 15002 and public["exit_code"] == 0
                assert len(public["chunk_id"]) == 6
                if requested is None:
                    assert public["output"] == raw
                else:
                    assert "Warning: truncated output" in public["output"]
                    assert len(public["output"]) < 160
                assert not result["is_error"] and not result.get("dispatch_error", False)
        finally:
            await runtime.aclose()
        runtime = create(thread)
        try:
            events = [e async for e in runtime.stream("continue")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert calls == (2 if poll and not nested else 1)
            with sqlite3.connect(database) as db:
                assert (
                    db.execute(
                        "SELECT result_json FROM tool_executions WHERE tool_name=?", (target,)
                    ).fetchone()[0]
                    == encoded
                )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
