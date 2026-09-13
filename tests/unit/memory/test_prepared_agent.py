import asyncio
import json

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.memory import (
    LongTermMemoryService,
    SQLiteMemoryRepository,
    agent,
    git_baseline,
    workspace,
)
from corki.memory.artifacts import (
    sync_stage_one_artifacts,
    write_baseline,
    write_consolidated_artifacts,
)
from corki.memory.models import ConsolidatedMemory
from corki.memory.permissions import MemoryPermissionSnapshot
from corki.models import ModelCompleted
from corki.protocol.items import AssistantMessageItem, new_step_id
from corki.storage import SQLiteSessionRepository


class Model:
    async def stream(self, request):
        yield ModelCompleted(
            (
                AssistantMessageItem(
                    json.dumps({"memory": "new", "memory_summary": "new", "skills": []}),
                    request.items[-1].turn_id,
                    new_step_id(),
                ),
            )
        )

    async def aclose(self):
        raise AssertionError("the worker borrows its model")


def service(tmp_path):
    database, root = tmp_path / "state.db", tmp_path / "memories"
    SQLiteSessionRepository(database)
    repository = SQLiteMemoryRepository(database)
    sync_stage_one_artifacts(root, ())
    return LongTermMemoryService(
        settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
        repository=repository,
        model=Model(),
        root=root,
    )


@pytest.mark.parametrize("unchanged", [False, True])
def test_one_configuration_before_inputs_and_same_workspace_for_worker(
    tmp_path, monkeypatch, unchanged, memory_worker_state_dirs
):
    async def scenario():
        instance = service(tmp_path)
        root = instance._root
        if unchanged:
            write_consolidated_artifacts(root, ConsolidatedMemory("old", "old"))
            write_baseline(root, workspace.digest(workspace.capture(root), outputs=False))
        order, roots, children = [], [], []
        original_worker = MemoryPermissionSnapshot.for_worker
        original_load = instance._repository.load_consolidation_inputs
        original_create = LangGraphRuntime.create

        async def prepare(snapshot, root):
            assert root == instance._root and root.is_dir()
            order.append("configuration")
            roots.append(root)
            return await original_worker(snapshot, root)

        async def load(**kwargs):
            assert order == ["configuration"]
            assert roots[0].exists()
            order.append("inputs")
            return await original_load(**kwargs)

        def create(**kwargs):
            child_settings = kwargs["settings"]
            assert child_settings.working_directory == roots[0]
            assert child_settings.model == instance._settings.resolved_memory_consolidation_model
            assert not child_settings.memories_enabled and not child_settings.mcp_servers
            children.append(child_settings)
            return original_create(**kwargs)

        monkeypatch.setattr(MemoryPermissionSnapshot, "for_worker", prepare)
        monkeypatch.setattr(instance._repository, "load_consolidation_inputs", load)
        monkeypatch.setattr(LangGraphRuntime, "create", create)
        claim = await instance._repository.claim_consolidation(lease_seconds=60)
        try:
            skipped = await instance._run_owned_consolidation(claim, MemoryPermissionSnapshot(None))
            assert skipped == unchanged
            assert order == ["configuration", "inputs"]
            assert len(children) == (0 if unchanged else 1)
            assert len(roots) == 1 and roots[0].is_dir()
            assert memory_worker_state_dirs and all(
                not path.exists() for path in memory_worker_state_dirs
            )
        finally:
            await instance.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["load", "sync", "capture", "baseline", "diff", "diff_file"])
def test_failures_after_configuration_reclaim_state_not_memory(
    tmp_path, monkeypatch, phase, memory_worker_state_dirs
):
    async def scenario():
        instance = service(tmp_path)
        roots = []
        original_worker = MemoryPermissionSnapshot.for_worker

        async def prepare(snapshot, root):
            roots.append(root)
            return await original_worker(snapshot, root)

        def fail(*args, **kwargs):
            raise OSError("injected " + phase)

        async def fail_async(*args, **kwargs):
            fail()

        monkeypatch.setattr(MemoryPermissionSnapshot, "for_worker", prepare)
        if phase == "load":
            monkeypatch.setattr(instance._repository, "load_consolidation_inputs", fail_async)
        elif phase == "sync":
            original_write = instance._repository.write_consolidation_workspace

            async def fail_sync_after_config(*args, **kwargs):
                if roots:
                    fail()
                return await original_write(*args, **kwargs)

            monkeypatch.setattr(
                instance._repository, "write_consolidation_workspace", fail_sync_after_config
            )
        else:
            owner, name = {
                "capture": (git_baseline, "capture"),
                "baseline": (git_baseline, "matches"),
                "diff": (workspace, "render_diff"),
                "diff_file": (agent, "write_workspace_diff"),
            }[phase]
            original = getattr(owner, name)

            def fail_after_config(*args, **kwargs):
                if roots:
                    fail()
                return original(*args, **kwargs)

            monkeypatch.setattr(owner, name, fail_after_config)
        claim = await instance._repository.claim_consolidation(lease_seconds=60)
        try:
            with pytest.raises(OSError, match="injected " + phase):
                await instance._run_owned_consolidation(claim, MemoryPermissionSnapshot(None))
            assert len(roots) == 1 and roots[0].is_dir()
            assert memory_worker_state_dirs and all(
                not path.exists() for path in memory_worker_state_dirs
            )
            assert not (instance._root / "MEMORY.md").exists()
        finally:
            await instance.aclose()

    asyncio.run(scenario())


def test_cancel_input_load_reclaims_state_not_memory(
    tmp_path, monkeypatch, memory_worker_state_dirs
):
    async def scenario():
        instance = service(tmp_path)
        entered, cancelled = asyncio.Event(), asyncio.Event()
        roots = []
        original_worker = MemoryPermissionSnapshot.for_worker

        async def prepare(snapshot, root):
            roots.append(root)
            return await original_worker(snapshot, root)

        async def load(**kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                assert roots[0].exists()
                cancelled.set()

        monkeypatch.setattr(MemoryPermissionSnapshot, "for_worker", prepare)
        monkeypatch.setattr(instance._repository, "load_consolidation_inputs", load)
        claim = await instance._repository.claim_consolidation(lease_seconds=60)
        task = asyncio.create_task(
            instance._run_owned_consolidation(claim, MemoryPermissionSnapshot(None))
        )
        try:
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert cancelled.is_set() and roots[0].is_dir()
            assert memory_worker_state_dirs and all(
                not path.exists() for path in memory_worker_state_dirs
            )
        finally:
            await instance.aclose()

    asyncio.run(scenario())
