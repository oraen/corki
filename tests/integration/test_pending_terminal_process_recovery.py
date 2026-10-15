"""Lose multiple in-memory terminal writes by exiting the actual owner process."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol import ThreadId
from corki.protocol.events import TurnCompleted
from corki.protocol.items import AssistantMessageItem, new_step_id
from corki.sessions import TurnStatus
from corki.tools import ToolRegistry


def _crash_with_pending_terminals(directory):
    async def scenario():
        root = Path(directory)
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(
                    (
                        AssistantMessageItem(
                            f"answer {len(requests)}", request.items[-1].turn_id, new_step_id()
                        ),
                    )
                )

            async def aclose(self):
                raise AssertionError("crash fixture must not gracefully close")

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(working_directory=root, skills_enabled=False),
            database_path=root / "process.db",
            home_path=root / "home",
            registry=ToolRegistry(),
            model=Model(),
        )
        save = runtime._repository.save_turn

        async def unavailable(record):
            if record.status is not TurnStatus.RUNNING:
                raise OSError("terminal storage unavailable")
            await save(record)

        runtime._repository.save_turn = unavailable
        runtime._repository.retry_turn_terminal = unavailable
        turns = []
        for message in ("first", "second", "third"):
            if message == "third":
                runtime._repository.save_turn = save
            events = [e async for e in runtime.stream(message)]
            assert isinstance(events[-1], TurnCompleted)
            turns.append(str(events[-1].turn_id))
        assert len(requests) == 3 and len(runtime._pending_terminals) == 2
        print(json.dumps({"thread": str(runtime.thread_id), "turns": turns}), flush=True)
        # This deliberately bypasses asyncio shutdown and all Runtime cleanup.
        os._exit(73)

    asyncio.run(scenario())


def test_multiple_pending_results_survive_owner_process_exit(tmp_path):
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            "import runpy,sys; "
            'runpy.run_path(sys.argv[1])["_crash_with_pending_terminals"](sys.argv[2])',
            str(Path(__file__).resolve()),
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert child.returncode == 73, (child.returncode, child.stdout, child.stderr)
    identity = json.loads(child.stdout)

    async def scenario():
        class NoSampling:
            async def stream(self, request):
                raise AssertionError("cold recovery resampled a completed task")
                yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "process.db",
            home_path=tmp_path / "home",
            registry=ToolRegistry(),
            model=NoSampling(),
            thread_id=ThreadId(identity["thread"]),
        )
        try:
            original = await runtime._repository.load_items(runtime.thread_id)
            recovered = []
            for _ in range(2):
                events = [e async for e in runtime.resume_pending()]
                assert isinstance(events[-1], TurnCompleted)
                recovered.append((str(events[-1].turn_id), events[-1].final_answer))
            assert recovered == [
                (identity["turns"][1], "answer 2"),
                (identity["turns"][0], "answer 1"),
            ]
            assert [e async for e in runtime.resume_pending()] == []
            assert await runtime._repository.load_items(runtime.thread_id) == original
            assert await runtime._repository.latest_running_turn(runtime.thread_id) is None
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
