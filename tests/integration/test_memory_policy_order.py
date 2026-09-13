import asyncio
import json
import sqlite3
from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.config.permissions import ExecutionPermissions
from corki.core import LangGraphRuntime
from corki.memory import agent, workspace
from corki.memory.artifacts import (
    sync_stage_one_artifacts,
    write_baseline,
    write_consolidated_artifacts,
)
from corki.memory.consolidation_prompt import seed_extension_instructions
from corki.memory.models import ConsolidatedMemory
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.items import AssistantMessageItem, new_step_id


@pytest.mark.parametrize("unchanged", [False, True])
def test_runtime_rejects_worker_config_before_sync_or_noop(tmp_path, monkeypatch, unchanged):
    async def scenario():
        root = tmp_path / "memories"
        sync_stage_one_artifacts(root, ())
        seed_extension_instructions(root)
        if unchanged:
            write_consolidated_artifacts(root, ConsolidatedMemory("old", "old"))
            write_baseline(root, workspace.digest(workspace.capture(root), outputs=False))
        else:
            (root / "raw_memories.md").write_text("must not be replaced before config admission")
        before = workspace.capture(root)
        inputs, copies = [], []
        mkdtemp = agent.tempfile.mkdtemp

        def allocate(*args, **kwargs):
            value = mkdtemp(*args, **kwargs)
            if kwargs.get("prefix") == "corki-memory-agent-":
                from pathlib import Path

                copies.append(Path(value))
            return value

        monkeypatch.setattr(agent.tempfile, "mkdtemp", allocate)
        for_worker = agent.MemoryPermissionSnapshot.for_worker

        async def invalid_worker(snapshot, root):
            permissions = ExecutionPermissions(
                tmp_path / "missing-compiler", tmp_path, json.dumps({"type": "read-only"})
            )
            return await for_worker(replace(snapshot, parent=permissions), root)

        monkeypatch.setattr(agent.MemoryPermissionSnapshot, "for_worker", invalid_worker)

        class Model:
            async def stream(self, request):
                yield ModelCompleted(
                    (AssistantMessageItem("parent", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class Never(Model):
            async def stream(self, request):
                raise AssertionError("invalid worker config must not sample")
                yield

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
            ),
            database_path=tmp_path / "state.db",
            memory_root=root,
            model=Model(),
            memory_model=Never(),
        )
        original = runtime._memory_repository.load_consolidation_inputs

        async def observe(**kwargs):
            inputs.append("selected")
            return await original(**kwargs)

        monkeypatch.setattr(runtime._memory_repository, "load_consolidation_inputs", observe)
        try:
            assert isinstance(
                [e async for e in runtime.stream("parent continues")][-1], TurnCompleted
            )
            report = await runtime._memory_service.wait()
            assert report.failed == 1 and not report.consolidation_skipped
            assert not inputs, "native policy failure precedes DB input selection"
            assert workspace.capture(root) == before
            assert copies and all(not path.exists() for path in copies)
            with sqlite3.connect(tmp_path / "state.db") as connection:
                row = connection.execute(
                    "SELECT status,ownership_token,lease_until,error FROM memory_jobs "
                    "WHERE job_key='global'"
                ).fetchone()
            assert row == ("failed", None, None, "failed_sandbox_policy")
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
