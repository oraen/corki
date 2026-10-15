"""Committed session store is not reconstructed from durable tool history."""

import asyncio

import pytest
from test_code_mode_lifecycle import invoke

from corki.code_mode.service import CodeModeService
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.items import AssistantMessageItem, ToolResultItem, UserMessageItem, new_step_id

pytestmark = pytest.mark.skipif(not CodeModeService.available(), reason="install corki[code-mode]")


def test_committed_store_survives_compaction_but_not_fork_or_cold_runtime(tmp_path):
    async def scenario():
        summary_prompt = "SUMMARIZE_STORE_SCOPE"
        scripts = []
        summaries = []

        class Model:
            calls = 0

            async def stream(self, request):
                user = next(i for i in reversed(request.items) if isinstance(i, UserMessageItem))
                if user.content == summary_prompt:
                    summaries.append(request)
                    yield ModelCompleted(
                        (
                            AssistantMessageItem(
                                "STORE HISTORY SUMMARY", user.turn_id, new_step_id()
                            ),
                        )
                    )
                    return
                self.calls += 1
                if self.calls % 2:
                    scripts.append(user.content)
                    yield invoke(request, "exec", user.content)
                else:
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        async def create(**kwargs):
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    tmp_path,
                    skills_enabled=False,
                    plugins_enabled=False,
                    tool_mode="code_mode_only",
                    compact_prompt=summary_prompt,
                ),
                model=Model(),
                database_path=tmp_path / "state.db",
                home_path=tmp_path,
                **kwargs,
            )

        async def run(runtime, script, expected):
            events = [e async for e in runtime.stream(script)]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            history = await runtime._repository.load_items(runtime.thread_id)
            result = next(i for i in reversed(history) if isinstance(i, ToolResultItem))
            assert not result.is_error, result.content
            assert expected in result.content

        source = await create()
        try:
            await run(source, 'store("scope", "SOURCE_VALUE"); text("written");', "written")
            prefix = await source._repository.load_items(source.thread_id)
            assert isinstance([e async for e in source.compact()][-1], TurnCompleted)
            await run(source, 'text(String(load("scope")));', "SOURCE_VALUE")
            original = await source._repository.load_items(source.thread_id)
            assert original[: len(prefix)] == prefix
            branch = await create(fork_from_thread_id=source.thread_id)
            try:
                assert [e async for e in branch.resume_pending()] == []
                await run(branch, 'text(String(load("scope")));', "undefined")
                await run(branch, 'store("scope", "BRANCH_VALUE"); text("written");', "written")
                assert await source._repository.load_items(source.thread_id) == original
            finally:
                await branch.aclose()
            await run(source, 'text(String(load("scope")));', "SOURCE_VALUE")
            thread = source.thread_id
            prefix = await source._repository.load_items(thread)
        finally:
            await source.aclose()

        cold = await create(thread_id=thread)
        try:
            assert [e async for e in cold.resume_pending()] == []
            await run(cold, 'text(String(load("scope")));', "undefined")
            history = await cold._repository.load_items(thread)
            assert history[: len(prefix)] == prefix
            assert len(scripts) == 6 and len(summaries) == 1
        finally:
            await cold.aclose()

    asyncio.run(scenario())
