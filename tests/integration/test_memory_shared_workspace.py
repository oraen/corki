import asyncio
import shlex
import sys

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.memory import git_baseline
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall


@pytest.mark.parametrize("fail", [False, True])
def test_consolidation_tools_write_shared_root_before_completion(tmp_path, fail):
    async def scenario():
        root = tmp_path / "memories"
        observed, requests = [], []
        script = (
            "from pathlib import Path; "
            "Path('MEMORY.md').write_text('v1\\nLIVE_MEMORY'); "
            "Path('memory_summary.md').write_text('v1\\nLIVE_SUMMARY'); "
            "print(Path.cwd())"
        )

        class Main:
            async def stream(self, request):
                yield ModelCompleted(
                    (AssistantMessageItem("parent", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class Memory(Main):
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    call = ToolCall(
                        new_tool_call_id(),
                        "exec_command",
                        {"cmd": shlex.join([sys.executable, "-I", "-c", script]), "login": False},
                    )
                    yield ModelCompleted((ToolCallItem(call, turn, step),))
                    return
                results = [i for i in request.items if isinstance(i, ToolResultItem)]
                observed.append(((root / "MEMORY.md").exists(), results[-1].content))
                if fail:
                    raise ValueError("injected worker failure after successful write")
                yield ModelCompleted((AssistantMessageItem("done", turn, step),))

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                memories_consolidation_model="fixture",
            ),
            model=Main(),
            memory_model=Memory(),
            memory_root=root,
            database_path=tmp_path / "state.db",
        )
        try:
            assert isinstance([e async for e in runtime.stream("parent")][-1], TurnCompleted)
            report = await runtime._memory_service.wait()
            assert observed and observed[0][0], (
                "tool writes must be visible before worker completion"
            )
            assert str(root) in observed[0][1], "worker cwd must be the actual shared memory root"
            assert (root / "MEMORY.md").read_text() == "v1\nLIVE_MEMORY"
            if fail:
                assert report.failed == 1 and not report.consolidated
                assert "MEMORY.md" not in git_baseline.read(root)
            else:
                assert report.consolidated and not report.failed
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_managed_deny_on_actual_memory_root_cannot_be_bypassed_by_copy(tmp_path):
    import json
    import os
    from pathlib import Path

    from corki.config.managed_mcp import MCPRequirementsLayer, compose_mcp_requirements
    from corki.config.permissions import ExecutionPermissions
    from corki.memory.permissions import MemoryPermissionSnapshot, MemorySandboxPolicyError

    compiler = os.environ.get("CORKI_TEST_SANDBOX_COMPILER")
    if not compiler or sys.platform != "darwin":
        pytest.skip("requires native policy compiler and macOS")

    async def scenario():
        root = tmp_path / "memories"
        secret = root / "extensions/team/secret.txt"
        secret.parent.mkdir(parents=True)
        secret.write_text("MANAGED_PRIVATE_MEMORY")
        sampled, leaked = [], []

        class Main:
            async def stream(self, request):
                yield ModelCompleted(
                    (AssistantMessageItem("parent", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class Memory(Main):
            async def stream(self, request):
                sampled.append(request)
                if len(sampled) == 1:
                    call = ToolCall(
                        new_tool_call_id(),
                        "exec_command",
                        {
                            "cmd": shlex.join(
                                [
                                    sys.executable,
                                    "-I",
                                    "-c",
                                    "from pathlib import Path; "
                                    "print(Path('extensions/team/secret.txt').read_text())",
                                ]
                            ),
                            "login": False,
                        },
                    )
                    yield ModelCompleted(
                        (ToolCallItem(call, request.items[-1].turn_id, new_step_id()),)
                    )
                    return
                leaked.extend(i.content for i in request.items if isinstance(i, ToolResultItem))
                yield ModelCompleted(
                    (
                        AssistantMessageItem(
                            json.dumps(
                                {
                                    "memory": "wrong root",
                                    "memory_summary": "wrong root",
                                    "skills": [],
                                }
                            ),
                            request.items[-1].turn_id,
                            new_step_id(),
                        ),
                    )
                )

        requirements = compose_mcp_requirements(
            (
                MCPRequirementsLayer(
                    "host", "[permissions.filesystem]\ndeny_read=[" + json.dumps(str(root)) + "]"
                ),
            )
        )
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                execution_permissions=ExecutionPermissions(
                    Path(compiler), tmp_path, '{"type":"read-only"}'
                ),
            ),
            model=Main(),
            memory_model=Memory(),
            memory_root=root,
            mcp_requirements=requirements,
            database_path=tmp_path / "state.db",
        )
        try:
            assert isinstance([e async for e in runtime.stream("parent")][-1], TurnCompleted)
            report = await runtime._memory_service.wait()
            # The exact same native compiler rejects the intended shared root.
            with pytest.raises(MemorySandboxPolicyError, match="readable root"):
                await MemoryPermissionSnapshot(
                    runtime._settings.execution_permissions, requirements
                ).for_worker(root)
            assert not any("MANAGED_PRIVATE_MEMORY" in content for content in leaked), leaked
            assert not sampled, "derived memory-root write policy conflicts with managed deny_read"
            assert report.failed == 1 and not report.consolidated
            assert any("failed_sandbox_policy" in w for w in runtime._memory_service.warnings)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
