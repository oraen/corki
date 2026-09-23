"""An interrupted MCP Hook must not repeat an unknown remote side effect."""

import asyncio
import json

import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.core.stop_hooks import command_identity
from corki.models import ModelCompleted
from corki.protocol.events import TurnCancelled
from corki.sessions import TurnStatus
from corki.tools import ToolRegistry


@pytest.mark.parametrize("result_committed", [False, True])
def test_interrupt_mcp_result_boundary_cold_recovery_never_repeats(
    tmp_path, monkeypatch, result_committed
):
    async def scenario():
        source = tmp_path / "config.toml"
        handler = {"type": "mcp_tool", "server": "policy", "tool": "record", "timeout": 1}
        fingerprint, _ = command_identity(handler, event_name="Interrupt")
        definition = (
            "[[hooks.Interrupt]]\n[[hooks.Interrupt.hooks]]\n"
            'type="mcp_tool"\nserver="policy"\ntool="record"\ntimeout=1\n'
            f"[hooks.state.{json.dumps(f'{source}:interrupt:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        settings = CorkiSettings(
            tmp_path,
            skills_enabled=False,
            plugins_enabled=False,
            mcp_servers=(MCPServerSettings("policy", "http", url="https://fixture.test"),),
            configuration=LocalConfigState((ConfigLayer(source, "user", contents=definition),)),
        )
        effects, samples = [], []
        started = asyncio.Event()

        class Client:
            def __init__(self, settings):
                self.settings = settings
                self.server_instructions = None
                self.is_closed = False

            async def start(self):
                pass

            async def list_tools(self):
                return ({"name": "record", "inputSchema": {"type": "object"}},)

            async def request(self, method, params):
                assert method == "tools/call"
                effects.append(params)
                return {"content": [{"type": "text", "text": "{}"}]}

            async def aclose(self):
                self.is_closed = True

        monkeypatch.setattr("corki.mcp.manager.create_client", Client)

        class Model:
            def __init__(self, cold):
                self.cold = cold

            async def stream(self, request):
                samples.append(self.cold)
                if not self.cold:
                    started.set()
                    await asyncio.Event().wait()
                yield ModelCompleted(())

            async def aclose(self):
                pass

        async def create(thread=None):
            return await LangGraphRuntime.acreate(
                settings=settings,
                model=Model(thread is not None),
                registry=ToolRegistry(),
                database_path=tmp_path / "state.db",
                home_path=tmp_path,
                thread_id=thread,
            )

        warm = await create()
        events = []

        async def consume(stream):
            try:
                async for event in stream:
                    events.append(event)
            except asyncio.CancelledError:
                pass

        original_save = warm._repository.save_turn
        original_complete = warm._repository.complete_hook_execution

        async def fail_terminal(record):
            if record.status == TurnStatus.CANCELLED:
                raise OSError("terminal unavailable")
            await original_save(record)

        async def fail_result(*args):
            if result_committed:
                await original_complete(*args)
            raise OSError("interrupt MCP result boundary")

        try:
            await warm._mcp_manager.start()
            with monkeypatch.context() as patch:
                patch.setattr(warm._repository, "save_turn", fail_terminal)
                patch.setattr(warm._repository, "retry_turn_terminal", fail_terminal)
                patch.setattr(warm._repository, "complete_hook_execution", fail_result)
                task = asyncio.create_task(consume(warm.stream("ORIGINAL")))
                async with asyncio.timeout(8):
                    await started.wait()
                    await warm.cancel_active()
                    await task
            assert isinstance(events[-1], TurnCancelled)
            thread, turn = warm.thread_id, events[-1].turn_id
            assert len(effects) == 1
            assert await warm._repository.load_turn_status(thread, turn) == TurnStatus.RUNNING
            facts = await warm._repository.load_hook_executions(thread, turn, "interrupt_hook:")
            assert len(facts) == 1
            assert (facts[0][2] is not None) == result_committed
        finally:
            warm._pending_terminals.clear()
            await warm.aclose()

        cold = await create(thread)
        try:
            events.clear()
            await consume(cold.resume_pending())
            assert isinstance(events[-1], TurnCancelled)
            assert samples == [False]
            assert len(effects) == 1
            saved_facts = await cold._repository.load_hook_executions(
                thread, turn, "interrupt_hook:"
            )
            assert saved_facts == facts
            assert await cold._repository.load_turn_status(thread, turn) == TurnStatus.CANCELLED
            assert [event async for event in cold.resume_pending()] == []
        finally:
            await cold.aclose()

    asyncio.run(scenario())
