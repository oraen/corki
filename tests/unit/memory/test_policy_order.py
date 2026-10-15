"""Red probes for native phase2 configuration-before-input ordering."""

import asyncio

import pytest

from corki.config import CorkiSettings
from corki.config.permissions import ExecutionPermissions
from corki.memory import LongTermMemoryService, SQLiteMemoryRepository, workspace
from corki.memory.artifacts import (
    sync_stage_one_artifacts,
    write_baseline,
    write_consolidated_artifacts,
)
from corki.memory.models import ConsolidatedMemory
from corki.memory.permissions import MemoryPermissionSnapshot, MemorySandboxPolicyError
from corki.storage import SQLiteSessionRepository


@pytest.mark.parametrize("unchanged", [False, True])
def test_policy_failure_precedes_input_load_and_noop_success(tmp_path, unchanged, monkeypatch):
    async def scenario():
        database, root = tmp_path / "state.db", tmp_path / "memories"
        SQLiteSessionRepository(database)
        repository = SQLiteMemoryRepository(database)
        sync_stage_one_artifacts(root, ())
        if unchanged:
            write_consolidated_artifacts(root, ConsolidatedMemory("old", "old"))
            write_baseline(root, workspace.digest(workspace.capture(root), outputs=False))
        inputs = []
        original = repository.load_consolidation_inputs

        async def observe(**kwargs):
            inputs.append("loaded")
            return await original(**kwargs)

        monkeypatch.setattr(repository, "load_consolidation_inputs", observe)
        configured = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            execution_permissions=ExecutionPermissions(
                tmp_path / "missing-backend", tmp_path, '{"type":"read-only"}'
            ),
        )

        class Never:
            async def stream(self, request):
                raise AssertionError("invalid policy must not sample")
                yield

            async def aclose(self):
                pass

        service = LongTermMemoryService(
            settings=configured, repository=repository, model=Never(), root=root
        )
        claim = await repository.claim_consolidation(lease_seconds=60)
        assert claim is not None
        try:
            with pytest.raises(MemorySandboxPolicyError):
                await service._run_owned_consolidation(
                    claim, MemoryPermissionSnapshot(configured.execution_permissions)
                )
            assert not inputs, "native rejects child config before selecting DB inputs"
        finally:
            await service.aclose()

    asyncio.run(scenario())
