import asyncio
import json
import sys
from pathlib import Path

import pytest

from corki.cli.application import CorkiApplication
from corki.config import CorkiPaths, CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("realtime", [False, True])
@pytest.mark.parametrize("phase", ["tools/list", "tools/call"])
@pytest.mark.parametrize("action", ["accept", "decline", "cancel"])
@pytest.mark.parametrize("nullable_schema", [False, True])
def test_cli_host_form_reaches_real_runtime_and_stdio_without_steering(
    tmp_path, realtime, phase, action, nullable_schema
):
    async def scenario():
        ordinary_entered = asyncio.Event()
        requests, notices, sampled = [], [], []
        readers, maximum, inputs = 0, 0, 0
        finished = False

        class Model:
            async def stream(self, request):
                sampled.append(request)
                assert "HOST_ONLY_VALUE" not in repr(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(sampled) == 1:
                    if realtime:
                        await ordinary_entered.wait()
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(new_tool_call_id(), "mcp__fixture::read", {}), turn, step
                            ),
                        )
                    )
                else:
                    observation = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    assert "host response received" in observation.content
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        class UI:
            def show_welcome(self):
                pass

            def show_goodbye(self):
                pass

            def show_notice(self, value):
                notices.append(value)

            async def read_message(self):
                nonlocal inputs, readers, maximum
                inputs += 1
                if inputs == 1:
                    return "needle"
                if finished:
                    raise EOFError
                readers += 1
                maximum = max(maximum, readers)
                ordinary_entered.set()
                try:
                    await asyncio.Future()
                finally:
                    readers -= 1

            async def read_elicitation(self, request):
                from corki.cli.elicitation import collect_elicitation

                nonlocal readers, maximum
                requests.append(request)
                assert request.params["requestedSchema"] == {
                    "type": "object",
                    "properties": {"label": {"type": "string"}},
                }
                assert readers == 0
                if phase == "tools/list":
                    # Optional startup may prompt after TurnStarted; the modal
                    # reader still excludes ordinary/realtime input and sampling.
                    assert not sampled
                readers += 1
                maximum = max(maximum, readers)
                answers = iter(["HOST_ONLY_VALUE", action])

                async def read(label):
                    await asyncio.sleep(0)
                    return next(answers)

                try:
                    return await collect_elicitation(request, read, notices.append)
                finally:
                    readers -= 1

            def show_tool_started(self, *args):
                pass

            def show_tool_completed(self, *args, **kwargs):
                pass

            def show_tool_output(self, value):
                assert "HOST_ONLY_VALUE" not in value

            def begin_assistant_message(self):
                pass

            def end_assistant_message(self):
                pass

            def append_assistant_delta(self, delta):
                pass

            def show_assistant_message(self, message, *, is_error=False):
                assert not is_error, message

        schema = {"type": "object", "properties": {"label": {"type": "string"}}}
        if nullable_schema:
            schema.update(required=None, title=None, description=None)
            schema["properties"]["label"].update(default=None, title=None, minLength=None)
        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            realtime_enabled=realtime,
            tool_search_mode="disabled",
            mcp_servers=(
                MCPServerSettings(
                    "fixture",
                    "stdio",
                    command=sys.executable,
                    args=(
                        "-u",
                        str(
                            Path(__file__).resolve().parents[1]
                            / "fixtures"
                            / "elicitation_mcp_server.py"
                        ),
                        phase,
                        json.dumps(schema),
                    ),
                ),
            ),
        )
        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
        )

        class Application(CorkiApplication):
            async def _consume_turn(self, message):
                nonlocal finished
                await super()._consume_turn(message)
                finished = True

        app = Application(settings, CorkiPaths.from_home(tmp_path / "home"), runtime, UI())
        try:
            assert await asyncio.wait_for(app.run(), 5) == 0
            assert len(requests) == 1 and len(sampled) == 2
            assert readers == 0 and maximum == 1
            assert not any("Runtime error" in n for n in notices)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
