import asyncio

import pytest

from corki.config import CorkiSettings
from corki.context.builder import ContextSnapshot
from corki.history_notes.service import HistoryNotesService
from corki.protocol.execution_identity import ExecutionIdentity
from corki.protocol.tools import ToolCall


@pytest.mark.parametrize("outcome", ["failure", "oversize", "cancel"])
def test_local_hint_failure_and_cancellation_contract(tmp_path, outcome):
    async def scenario():
        class HintBackend(Backend):
            async def call(self, *args, **kwargs):
                self.calls.append(args)
                if outcome == "cancel":
                    raise asyncio.CancelledError
                if outcome == "failure":
                    raise ValueError("storage unavailable")
                return {"text": "界" * 1334}

        backend = HintBackend()
        service = HistoryNotesService(CorkiSettings(tmp_path), "thread", backend=backend)
        service.bind_identity(ExecutionIdentity("thread", "session"))
        snapshot = ContextSnapshot("", (), tmp_path)
        if outcome == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await service.decorate(snapshot, [], "turn")
            assert service._hint_window is None
        else:
            for _ in range(2):
                result = await service.decorate(snapshot, [], "turn")
                assert not any(item.key == "notes.thread_hint" for item in result.items)
            assert len(backend.calls) == 1

    asyncio.run(scenario())


class Backend:
    def __init__(self):
        self.calls = []

    async def call(self, action, arguments, *, session_id, budget):
        self.calls.append((action, session_id))
        return {"text": "hint"}

    async def aclose(self):
        pass


@pytest.mark.parametrize("operation", ["hint", "tool"])
def test_uninitialized_recovery_refuses_identity_guess_before_backend(tmp_path, operation):
    async def scenario():
        backend = Backend()
        service = HistoryNotesService(CorkiSettings(tmp_path), "thread", backend=backend)
        with pytest.raises(RuntimeError, match="identity is not initialized"):
            if operation == "hint":
                await service.decorate(ContextSnapshot("", (), tmp_path), [], "turn")
            elif operation == "tool":
                tool = service.tools[0]
                await tool.execute(ToolCall("call", tool.spec.name, {}), None)
        assert backend.calls == []

    asyncio.run(scenario())


@pytest.mark.parametrize("invalid", [None, "session", ExecutionIdentity("other", "session")])
def test_recovery_binding_rejects_invalid_identity(tmp_path, invalid):
    service = HistoryNotesService(CorkiSettings(tmp_path), "thread", backend=Backend())
    with pytest.raises((ValueError, TypeError)):
        service.bind_identity(invalid)
    service.bind_identity(ExecutionIdentity("thread", "session"))
    assert service._require_identity().session_id == "session"


def test_all_recovery_consumers_share_one_immutable_binding(tmp_path):
    async def scenario():
        backend = Backend()
        service = HistoryNotesService(CorkiSettings(tmp_path), "thread", backend=backend)
        identity = ExecutionIdentity("thread", "session")
        service.bind_identity(identity)
        service.bind_identity(ExecutionIdentity("thread", "session"))
        await service.decorate(ContextSnapshot("", (), tmp_path), [], "turn")
        for tool in service.tools:
            await tool.execute(ToolCall("call", tool.spec.name, {}), None)
        with pytest.raises(RuntimeError, match="already bound"):
            service.bind_identity(ExecutionIdentity("thread", "replacement"))
        assert backend.calls == [("notes::thread_hint", "session")] + [
            (tool.spec.name, "session") for tool in service.tools
        ]

    asyncio.run(scenario())
