"""Real Runtime regression cases for the native memory workspace input contract."""

import asyncio
import json
import os
import sqlite3
import subprocess

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.items import AssistantMessageItem, new_step_id


@pytest.mark.parametrize("case", ["git", "hidden", "fifo", "symlink"])
def test_runtime_memory_uses_git_workspace_inputs(tmp_path, case):
    async def scenario():
        root, database = tmp_path / "memories", tmp_path / "state.db"
        calls = []

        class Main:
            async def stream(self, request):
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class Memory(Main):
            async def stream(self, request):
                calls.append(request)
                yield ModelCompleted(
                    (
                        AssistantMessageItem(
                            json.dumps(
                                {"memory": "memory", "memory_summary": "route", "skills": []}
                            ),
                            request.items[-1].turn_id,
                            new_step_id(),
                        ),
                    )
                )

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, memories_enabled=True
            ),
            model=Main(),
            memory_model=Memory(),
            memory_root=root,
            database_path=database,
        )
        try:
            assert isinstance([e async for e in runtime.stream("first")][-1], TurnCompleted)
            assert (await runtime._memory_service.wait()).consolidated
            if case == "git":
                assert (root / ".git/index").is_file(), "a successful pass must publish a Git index"
                result = subprocess.run(
                    ["git", "-C", str(root), "rev-list", "--count", "HEAD"],
                    capture_output=True,
                    check=True,
                    timeout=10,
                )
                assert result.stdout.strip() == b"1"
                assert not (root / ".consolidation-baseline.json").exists()
                return
            if case == "hidden":
                (root / ".hidden-note").write_text("NEW HIDDEN INPUT")
            elif case == "fifo":
                if not hasattr(os, "mkfifo"):
                    pytest.skip("requires POSIX FIFO")
                os.mkfifo(root / "pipe")
                (root / "note.md").write_text("NEW INPUT")
            else:
                outside = tmp_path / "outside"
                outside.write_text("MUST NOT READ")
                (root / "link").symlink_to(outside)
                (root / "note.md").write_text("NEW INPUT")
            with sqlite3.connect(database) as db:
                db.execute("UPDATE memory_jobs SET finished_at=0 WHERE job_key='global'")
            assert isinstance([e async for e in runtime.stream("next")][-1], TurnCompleted)
            report = await runtime._memory_service.wait()
            assert report.consolidated and not report.failed, report
            assert len(calls) == 2
            if case == "symlink":
                assert not (root / "link").is_symlink()
                assert outside.read_text() == "MUST NOT READ"
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
