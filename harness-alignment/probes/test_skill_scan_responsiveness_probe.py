"""A slow filesystem scan must not block the Runtime's event loop."""

import asyncio
import threading

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.items import AssistantMessageItem, new_step_id
from corki.tools import ToolRegistry


def test_skill_scan_allows_other_runtime_tasks_to_progress(tmp_path, monkeypatch):
    async def scenario():
        import corki.skills.service as service_module

        folder = tmp_path / ".corki/skills"
        (folder / "guide").mkdir(parents=True)
        (folder / "guide/SKILL.md").write_text("---\nname: guide\ndescription: fixture\n---\nBODY")
        entered, release = threading.Event(), threading.Event()
        observed = []
        original = service_module.scan_skill_root

        def scan(root):
            if root.path == folder and not entered.is_set():
                entered.set()
                observed.append(release.wait(0.5))
            return original(root)

        monkeypatch.setattr(service_module, "scan_skill_root", scan)

        class Model:
            async def stream(self, request):
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path),
            model=Model(),
            registry=ToolRegistry(),
            home_path=tmp_path / "home",
            database_path=tmp_path / "runtime.db",
        )

        async def peer():
            while not entered.is_set():
                await asyncio.sleep(0.001)
            release.set()

        peer_task = asyncio.create_task(peer())
        try:
            events = [event async for event in runtime.stream("Use $guide")]
            await asyncio.wait_for(peer_task, 2)
            assert isinstance(events[-1], TurnCompleted)
            assert observed == [True], "the event-loop peer must run before slow IO times out"
        finally:
            release.set()
            peer_task.cancel()
            await asyncio.gather(peer_task, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())
