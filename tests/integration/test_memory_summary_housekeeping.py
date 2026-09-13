"""Shared-root summary deletion and external recreation through real consolidation tools."""

import asyncio
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.memory import SQLiteMemoryRepository, git_baseline
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.storage import SQLiteSessionRepository


@pytest.mark.parametrize("conflict", [False, True])
def test_worker_deletion_is_immediate_and_does_not_redelete_recreated_summary(tmp_path, conflict):
    async def scenario():
        database, root = tmp_path / "sessions.db", tmp_path / "memories"
        sessions = SQLiteSessionRepository(database)
        repository = SQLiteMemoryRepository(database)
        source = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        with sqlite3.connect(database) as db:
            db.execute(
                "INSERT INTO threads(id,cwd,updated_at,memory_mode) "
                "VALUES ('source',?,?,'enabled')",
                (str(tmp_path), source),
            )
            db.execute(
                "INSERT INTO memory_stage1_outputs(thread_id,cwd,source_updated_at,raw_memory,"
                "rollout_summary) VALUES ('source',?,?,'raw fact','redundant summary')",
                (str(tmp_path), source),
            )
            db.execute(
                "INSERT INTO memory_jobs(kind,job_key,status,source_updated_at,"
                "last_success_source_updated_at) VALUES ('memory_stage1','source','succeeded',?,?)",
                (source, source),
            )
        await repository.close()
        await sessions.close()
        requests, removed = [], []

        class Main:
            async def stream(self, request):
                yield ModelCompleted(
                    (AssistantMessageItem("ready", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class Memory:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    environment = next(
                        i for i in request.items if getattr(i, "key", None) == "environment.primary"
                    )
                    cwd = Path(re.search(r"<cwd>(.*?)</cwd>", environment.content, re.S)[1])
                    summary = next((cwd / "rollout_summaries").glob("*.md"))
                    name = summary.relative_to(cwd).as_posix()
                    removed.append(name)
                    patch = (
                        "*** Begin Patch\n*** Delete File: " + name + "\n"
                        "*** Add File: MEMORY.md\n+# Task Group: retained\n+scope: fixture\n"
                        "*** Add File: memory_summary.md\n+v1\n+retained index\n*** End Patch"
                    )
                    call = ToolCall(new_tool_call_id(), "apply_patch", {"patch": patch})
                    yield ModelCompleted((ToolCallItem(call, turn, step),))
                else:
                    result = next(
                        i for i in reversed(request.items) if isinstance(i, ToolResultItem)
                    )
                    assert not result.is_error, result.content
                    assert not (root / removed[0]).exists(), "tool deletion is already visible"
                    if conflict:
                        (root / removed[0]).write_text("CONCURRENT_SOURCE_EDIT\n")
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                memories_consolidation_model="fixture",
            ),
            database_path=database,
            memory_root=root,
            model=Main(),
            memory_model=Memory(),
        )
        try:
            assert isinstance([e async for e in runtime.stream("work")][-1], TurnCompleted)
            report = await runtime._memory_service.wait()
            assert len(requests) == 2
            assert report.consolidated and not report.failed, runtime._memory_service.warnings
            if conflict:
                assert (root / removed[0]).read_text() == "CONCURRENT_SOURCE_EDIT\n"
                assert (root / "MEMORY.md").is_file()
                baseline = git_baseline.read(root)
                assert removed[0] in baseline
            else:
                assert not (root / removed[0]).exists()
                baseline = git_baseline.read(root)
                assert removed[0] not in baseline
                assert (root / "MEMORY.md").read_text().startswith("# Task Group:")
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
