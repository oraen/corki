"""Persisted MCP hook results survive cold Runtime without replaying effects."""

import asyncio
import json
from dataclasses import replace

import pytest
from test_code_mode import request_call

from corki.config import CorkiSettings, MCPServerSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.core.stop_hooks import command_identity
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_turn_id
from corki.protocol.items import ContextItem, ToolResultItem, UserMessageItem
from corki.protocol.tools import ToolResult, ToolSpec
from corki.sessions import TurnRecord, TurnStatus
from corki.tools import ToolRegistry


@pytest.mark.parametrize("event", ["PreToolUse", "PostToolUse"])
@pytest.mark.parametrize("window", ["unknown", "completed"])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("remove_configuration", [False, True])
def test_cold_mcp_hook_never_repeats_claimed_call(
    tmp_path, monkeypatch, event, window, nested, remove_configuration
):
    async def scenario():
        source = tmp_path / "config.toml"
        handler = {"type": "mcp_tool", "server": "policy", "tool": "review"}
        fingerprint, _ = command_identity(handler, event_name=event, matcher="probe")
        key = "pre_tool_use" if event == "PreToolUse" else "post_tool_use"
        document = (
            f'[[hooks.{event}]]\nmatcher="probe"\n[[hooks.{event}.hooks]]\n'
            'type="mcp_tool"\nserver="policy"\ntool="review"\n'
            f"[hooks.state.{json.dumps(f'{source}:{key}:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        settings = CorkiSettings(
            tmp_path,
            skills_enabled=False,
            plugins_enabled=False,
            tool_mode="code_mode_only" if nested else "direct",
            mcp_servers=(MCPServerSettings("policy", "http", url="https://fixture.invalid"),),
            configuration=LocalConfigState((ConfigLayer(source, "user", contents=document),)),
        )
        calls, effects, requests = [], [], []

        class Client:
            is_closed = False
            server_instructions = None

            def __init__(self, settings):
                self.settings = settings

            async def start(self):
                pass

            async def list_tools(self):
                return ({"name": "review", "inputSchema": {"type": "object"}},)

            async def request(self, method, params):
                assert method == "tools/call"
                calls.append(params)
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "hookSpecificOutput": {
                                        "hookEventName": event,
                                        "additionalContext": "SAVED_MCP",
                                    }
                                }
                            ),
                        }
                    ]
                }

            async def aclose(self):
                self.is_closed = True

        monkeypatch.setattr("corki.mcp.manager.create_client", Client)

        class Probe:
            spec = ToolSpec("probe", "side effect", {"type": "object"})

            async def execute(self, call, context):
                effects.append(call.id)
                return ToolResult(call.id, call.name, "ORIGINAL_RESULT")

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 1:
                    yield request_call(
                        request,
                        "exec" if nested else "probe",
                        "text(await tools.probe({}));" if nested else {},
                    )
                else:
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        class Sink:
            async def emit(self, event):
                pass

        async def create(configuration, thread=None):
            registry = ToolRegistry()
            registry.register(Probe())
            return await LangGraphRuntime.acreate(
                settings=configuration,
                registry=registry,
                model=Model(),
                database_path=tmp_path / "state.db",
                home_path=tmp_path,
                thread_id=thread,
            )

        warm = await create(settings)
        try:
            await warm._ensure_ready()
            await warm._mcp_manager.start()
            thread, turn = warm.thread_id, new_turn_id()
            user = UserMessageItem("run probe", turn)
            repository = warm._repository
            await repository.save_turn(TurnRecord(turn, thread, TurnStatus.RUNNING, user.content))
            await repository.append_items(thread, (user,))
            entered = asyncio.Event()
            complete = repository.complete_hook_execution

            async def held_complete(*args):
                if window == "completed":
                    await complete(*args)
                entered.set()
                await asyncio.Event().wait()

            with monkeypatch.context() as patch:
                patch.setattr(repository, "complete_hook_execution", held_complete)
                task = asyncio.create_task(
                    warm._compiled.ainvoke(
                        _initial_state(thread, turn, settings, user),
                        context=GraphRunContext(events=Sink()),
                        config=warm._graph_config(turn),
                        durability="sync",
                    )
                )
                try:
                    await asyncio.wait_for(entered.wait(), 5)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            prefix = f"{key}:{thread}:{turn}:" if event == "PostToolUse" else f"{key}:{turn}:"
            facts = await repository.load_hook_executions(thread, turn, prefix)
            assert len(facts) == len(calls) == len(requests) == 1
            assert (facts[0][2] is None) == (window == "unknown")
            assert facts[0][1]["mcp"]["tool"] == "review"
        finally:
            await warm.aclose()

        if remove_configuration:
            settings = replace(settings, configuration=LocalConfigState(()), mcp_servers=())
        cold = await create(settings, thread)
        try:
            events = [e async for e in cold.resume_pending()]
            assert isinstance(events[-1], TurnCompleted)
            assert len(calls) == 1 and len(requests) == 2
            assert len(effects) == (1 if event == "PostToolUse" else 0)
            feedback = [
                i
                for i in requests[-1].items
                if isinstance(i, ContextItem)
                and i.content_kind == "hooks.additional_context"
                and i.content == "SAVED_MCP"
            ]
            assert len(feedback) == (1 if window == "completed" else 0)
            result = next(i for i in requests[-1].items if isinstance(i, ToolResultItem))
            assert result.is_error == (nested or event == "PreToolUse" or window == "unknown")
            assert await cold._repository.load_hook_executions(thread, turn, prefix) == facts
            before = await cold._repository.load_items(thread)
            assert [e async for e in cold.resume_pending()] == []
            assert await cold._repository.load_items(thread) == before
            assert len(calls) == 1
        finally:
            await cold.aclose()

    asyncio.run(scenario())
