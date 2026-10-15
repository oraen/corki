"""A configured older helper cannot silently skip the requested metadata semantics."""

import asyncio
import os
from pathlib import Path

import pytest
from test_execution_policy_live_inheritance import Model, run

from corki.config import CorkiSettings
from corki.config.permissions import ExecutionPermissions
from corki.context.permissions import PermissionContext
from corki.core import LangGraphRuntime


@pytest.mark.parametrize("entry", ["runtime", "context"])
def test_old_compiler_rejected_before_sampling_or_publishing_context(tmp_path, entry):
    old = os.environ.get("CORKI_TEST_PRE_METADATA_COMPILER")
    if not old:
        pytest.skip("requires an actual pre-product-metadata compiler")
    permissions = ExecutionPermissions(Path(old), tmp_path, '{"type":"read-only"}')

    async def scenario():
        if entry == "context":
            with pytest.raises(ValueError, match="unknown field `corki_metadata`"):
                await PermissionContext().snapshot(permissions, tmp_path, honor_allow_rules=True)
            return
        model = Model("direct")
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                execution_permissions=permissions,
            ),
            model=model,
            database_path=tmp_path / "state.db",
            home_path=tmp_path / "host",
        )
        try:
            with pytest.raises(ValueError, match="unknown field `corki_metadata`"):
                await run(runtime, model)
            assert not model.requests
            assert await runtime._repository.latest_thread() is None
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
