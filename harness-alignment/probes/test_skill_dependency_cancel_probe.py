"""Runtime cancellation owns and joins a blocked dependency configuration write."""

import asyncio
import threading

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCancelled, TurnCompleted
from corki.tools import ToolRegistry


@pytest.mark.parametrize("late_failure", [False, True])
def test_repeated_cancel_joins_install_writer_and_preserves_cancel(
    tmp_path, monkeypatch, late_failure
):
    async def scenario():
        skill = tmp_path / ".corki/skills/install-guide/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: install-guide\ndescription: fixture\n---\nBODY")
        metadata = skill.parent / "agents/openai.yaml"
        metadata.parent.mkdir()
        metadata.write_text(
            "dependencies:\n  tools:\n    - type: mcp\n      value: held\n"
            "      transport: stdio\n      command: never-executed\n"
        )
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()

        def write(*args):
            entered.set()
            assert release.wait(5)
            finished.set()
            if late_failure:
                raise PermissionError("late fixture failure")
            return ("held",)

        def no_client(*args, **kwargs):
            raise AssertionError("cancelled install must not publish/start a client")

        monkeypatch.setattr("corki.skills.mcp_dependencies.add_missing_mcp_servers", write)
        monkeypatch.setattr("corki.mcp.manager.create_client", no_client)
        monkeypatch.setattr("corki.mcp.manager.HttpMCPClient", no_client)

        class Model:
            async def stream(self, request):
                raise AssertionError("cancelled installation must not reach the model")
                yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(tmp_path, execution_permissions=None),
            model=Model(),
            registry=ToolRegistry(),
            home_path=tmp_path / "home",
            database_path=tmp_path / "session.db",
        )
        events = []

        async def consume():
            async for event in runtime.stream("$install-guide"):
                events.append(event)

        task = asyncio.create_task(consume())
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            task.cancel()
            await asyncio.sleep(0.02)
            task.cancel()
            await asyncio.sleep(0.02)
            assert not task.done() and not finished.is_set()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert finished.is_set()
            assert sum(isinstance(event, TurnCancelled) for event in events) == 1
            assert not any(isinstance(event, TurnCompleted) for event in events)
            assert not runtime._mcp_manager.skill_dependency_catalog().servers
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(asyncio.wait_for(scenario(), 10))
