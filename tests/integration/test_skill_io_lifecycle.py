"""Slow skill IO at every Runtime entry point stays responsive and owned on close."""

import asyncio
import threading

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCancelled, TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    ToolCallItem,
    ToolResultItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall
from corki.skills.service import SkillService
from corki.tools import ToolRegistry


@pytest.mark.parametrize(
    "path", ["requirements", "catalog", "input_body", "skill_list", "skill_read"]
)
@pytest.mark.parametrize("close", [False, True], ids=["complete", "close"])
def test_slow_skill_io_runtime_lifecycle(tmp_path, monkeypatch, path, close):
    async def scenario():
        import corki.skills.service as module

        folder = tmp_path / ".corki/skills/guide"
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text("---\nname: guide\ndescription: fixture\n---\nSKILL_BODY")
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        observed, requests = [], []
        armed = path not in {"skill_list", "skill_read"}

        def gate():
            if armed and not started.is_set():
                started.set()
                try:
                    observed.append(release.wait(3))
                finally:
                    finished.set()

        if path == "requirements":
            original = module.scan_skill_root

            def scan(root):
                if root.path == folder.parent:
                    gate()
                return original(root)

            monkeypatch.setattr(module, "scan_skill_root", scan)
        else:
            method = (
                "catalog" if path == "catalog" else "snapshot" if path == "skill_list" else "read"
            )
            original = getattr(SkillService, method)

            def read(self, *args, **kwargs):
                gate()
                return original(self, *args, **kwargs)

            monkeypatch.setattr(SkillService, method, read)

        class Model:
            async def stream(self, request):
                nonlocal armed
                requests.append(request)
                turn = request.items[-1].turn_id
                if path in {"skill_list", "skill_read"} and len(requests) == 1:
                    armed = True
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(
                                    new_tool_call_id(),
                                    path,
                                    {"name": "guide"} if path == "skill_read" else {},
                                ),
                                turn,
                                new_step_id(),
                            ),
                        )
                    )
                    return
                yield ModelCompleted((AssistantMessageItem("done", turn, new_step_id()),))

            async def aclose(self):
                assert finished.is_set(), "model closed before the skill worker was joined"

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path),
            model=Model(),
            registry=ToolRegistry(),
            home_path=tmp_path / "home",
            database_path=tmp_path / "runtime.db",
        )
        events = []

        async def consume():
            async for event in runtime.stream("Use $guide" if path == "input_body" else "hello"):
                events.append(event)

        async def entered():
            while not started.is_set():
                await asyncio.sleep(0.001)

        run = asyncio.create_task(consume())
        closing = None
        try:
            await asyncio.wait_for(entered(), 2)
            assert not finished.is_set(), "slow IO blocked the event loop until its timeout"
            if close:
                closing = asyncio.create_task(runtime.aclose())
                # Let close cancel the active Turn and enter owned-worker cleanup.
                for _ in range(10):
                    await asyncio.sleep(0)
                assert not closing.done()
                assert not run.done()
            release.set()
            if close:
                # Runtime yields the durable cancellation terminal, then raises
                # cancellation to its host consumer (runtime._run_graph).
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(run, 3)
            else:
                await asyncio.wait_for(run, 3)
            if closing is not None:
                await asyncio.wait_for(closing, 3)
            assert observed == [True]
            assert finished.is_set()
            assert isinstance(events[-1], TurnCancelled if close else TurnCompleted)
            assert sum(isinstance(e, (TurnCancelled, TurnCompleted)) for e in events) == 1
            if close:
                assert len(requests) == (1 if path.startswith("skill_") else 0)
            elif path.startswith("skill_"):
                results = [i for i in requests[-1].items if isinstance(i, ToolResultItem)]
                assert len(results) == 1 and results[0].tool_name == path
                assert "guide" in results[0].content
                if path == "skill_read":
                    assert "SKILL_BODY" in results[0].content
            elif path == "input_body":
                assert any(
                    isinstance(i, ContextItem) and "SKILL_BODY" in i.content
                    for i in requests[-1].items
                )
        finally:
            release.set()
            await asyncio.gather(run, return_exceptions=True)
            if closing is not None:
                await asyncio.gather(closing, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())
