import asyncio

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.items import AssistantMessageItem, ContextItem, new_step_id


@pytest.mark.parametrize("case", ["default", "override", "empty_override"])
def test_root_runtime_loads_global_home_instructions_separately_from_project(tmp_path, case):
    async def scenario():
        home, project = tmp_path / "home", tmp_path / "project"
        home.mkdir()
        project.mkdir()
        (project / ".git").mkdir()
        (home / "AGENTS.md").write_text("GLOBAL_DEFAULT_INSTRUCTION")
        (project / "AGENTS.md").write_text("PROJECT_LOCAL_INSTRUCTION")
        if case != "default":
            (home / "AGENTS.override.md").write_text(
                "GLOBAL_OVERRIDE_INSTRUCTION" if case == "override" else "  \n\t"
            )
        requests = []

        class Main:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(working_directory=project, skills_enabled=False),
            home_path=home,
            model=Main(),
            database_path=tmp_path / "state.db",
        )
        try:
            assert isinstance([e async for e in runtime.stream("hello")][-1], TurnCompleted)
            text = "\n".join(i.content for i in requests[-1].items)
            assert "PROJECT_LOCAL_INSTRUCTION" in text
            expected = (
                "GLOBAL_OVERRIDE_INSTRUCTION"
                if case == "override"
                else "GLOBAL_DEFAULT_INSTRUCTION"
            )
            assert expected in text, (
                "the host's independent global provider must reach the root request"
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("removed", [False, True])
def test_project_creation_snapshot_and_cold_refresh_preserve_history(tmp_path, removed):
    async def scenario():
        project = tmp_path / "project"
        project.mkdir()
        (project / ".git").mkdir()
        source = project / "AGENTS.md"
        source.write_text("CREATION_RULE")
        requests = []

        class Main:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(working_directory=project, skills_enabled=False),
            home_path=tmp_path / "home",
            model=Main(),
            database_path=tmp_path / "state.db",
        )
        try:
            assert isinstance([e async for e in runtime.stream("first")][-1], TurnCompleted)
            if removed:
                source.unlink()
            else:
                source.write_text("UNRELOADED_FILE_CHANGE")
            assert isinstance([e async for e in runtime.stream("second")][-1], TurnCompleted)
            text = "\n".join(i.content for i in requests[-1].items)
            assert "CREATION_RULE" in text
            assert "UNRELOADED_FILE_CHANGE" not in text
            original = await runtime._repository.load_items(runtime.thread_id)
        finally:
            await runtime.aclose()

        cold = await LangGraphRuntime.acreate(
            settings=runtime._settings,
            home_path=tmp_path / "home",
            model=Main(),
            database_path=tmp_path / "state.db",
            thread_id=runtime.thread_id,
        )
        try:
            assert [e async for e in cold.resume_pending()] == []
            assert len(requests) == 2
            assert isinstance([e async for e in cold.stream("third")][-1], TurnCompleted)
            sections = [
                i
                for i in requests[-1].items
                if isinstance(i, ContextItem) and i.key == "project.agents"
            ]
            assert len(sections) == 2
            assert "CREATION_RULE" in sections[0].content
            notice = (
                "The previously provided AGENTS.md instructions no longer apply."
                if removed
                else (
                    "These AGENTS.md instructions replace all previously provided "
                    "AGENTS.md instructions."
                )
            )
            assert notice in sections[1].content
            assert ("UNRELOADED_FILE_CHANGE" in sections[1].content) is not removed
            history = await cold._repository.load_items(cold.thread_id)
            assert history[: len(original)] == original
            assert isinstance([e async for e in cold.stream("fourth")][-1], TurnCompleted)
            assert [
                i
                for i in requests[-1].items
                if isinstance(i, ContextItem) and i.key == "project.agents"
            ] == sections
        finally:
            await cold.aclose()

    asyncio.run(scenario())


def test_managed_deny_read_of_project_instructions_fails_before_sampling(tmp_path):
    import json
    import os
    import sys
    from pathlib import Path

    from corki.config.managed_mcp import MCPRequirementsLayer, compose_mcp_requirements
    from corki.config.permissions import ExecutionPermissions

    compiler = os.environ.get("CORKI_TEST_SANDBOX_COMPILER")
    if not compiler or sys.platform != "darwin":
        pytest.skip("requires native macOS compiler")

    async def scenario():
        project = tmp_path / "project"
        project.mkdir()
        (project / ".git").mkdir()
        source = project / "AGENTS.md"
        source.write_text("MANAGED_DENIED_PROJECT_INSTRUCTION")
        requests = []

        class Main:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        requirements = compose_mcp_requirements(
            (
                MCPRequirementsLayer(
                    "host", "[permissions.filesystem]\ndeny_read=[" + json.dumps(str(source)) + "]"
                ),
            )
        )
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=project,
                skills_enabled=False,
                execution_permissions=ExecutionPermissions(
                    Path(compiler), project, '{"type":"read-only"}'
                ),
            ),
            home_path=tmp_path / "home",
            model=Main(),
            database_path=tmp_path / "state.db",
            mcp_requirements=requirements,
        )
        try:
            try:
                events = [e async for e in runtime.stream("first")]
            except (ValueError, OSError):
                events = []
            leaked = any(
                "MANAGED_DENIED_PROJECT_INSTRUCTION" in i.content for r in requests for i in r.items
            )
            assert not leaked, "host instruction discovery bypassed the actual managed deny_read"
            assert not requests
            assert not events or not isinstance(events[-1], TurnCompleted)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
