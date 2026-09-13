import asyncio
import threading

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.memory import agent, workspace
from corki.memory.consolidation_prompt import build_consolidation_prompt
from corki.models import ModelCompleted
from corki.prompting import PromptStore
from corki.protocol.items import AssistantMessageItem, new_step_id


class Model:
    async def stream(self, request):
        yield ModelCompleted(
            (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
        )

    async def aclose(self):
        raise AssertionError("the child must not close its borrowed transport")


@pytest.mark.parametrize("phase", ["_prepare", "collect"])
def test_cancellation_joins_filesystem_worker_before_removing_copy(tmp_path, monkeypatch, phase):
    async def scenario():
        (tmp_path / "MEMORY.md").write_text("v1\nMemory")
        (tmp_path / "memory_summary.md").write_text("v1\nIndex")
        sampled = workspace.capture(tmp_path)
        entered, release = asyncio.Event(), threading.Event()
        original = getattr(agent, phase)
        copies = []
        loop = asyncio.get_running_loop()

        def held(root, *args):
            copies.append(root.parent)
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5), "fixture was not released"
            assert root.parent.exists()
            return original(root, *args)

        monkeypatch.setattr(agent, phase, held)
        task = asyncio.create_task(
            agent.run_agent(
                settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
                model=Model(),
                sampled=sampled,
                instructions="Worker",
                workspace_diff="full diff",
            )
        )
        try:
            await asyncio.wait_for(entered.wait(), 3)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and copies[0].exists()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert all(not path.exists() for path in copies)

    asyncio.run(scenario())


def test_tool_readable_diff_is_complete_but_not_inlined_in_policy(tmp_path):
    root = tmp_path / "memories"
    root.mkdir()
    (root / "large.txt").write_text("source line\n" * 8000)
    sampled = workspace.capture(root)
    full_diff = workspace.render_diff(None, sampled)
    staged = tmp_path / "staged"
    agent._prepare(staged, sampled, full_diff)
    assert (staged / "phase2_workspace_diff.md").read_text() == full_diff
    prompt = build_consolidation_prompt(PromptStore(), staged)
    assert "source line" not in prompt
    assert "phase2_workspace_diff.md" in prompt


@pytest.mark.parametrize("source_size", [4000, 500000, 0])
def test_source_size_does_not_inflate_consolidation_policy(tmp_path, source_size):
    root = tmp_path / "memories"
    root.mkdir()
    before = build_consolidation_prompt(PromptStore(), root)
    (root / "source.txt").write_text("LAZY_SOURCE\n" * source_size)
    assert build_consolidation_prompt(PromptStore(), root) == before


@pytest.mark.parametrize("override", [None, 6000])
@pytest.mark.parametrize("window", [3000, 4000])
def test_worker_runtime_resolves_its_own_model_window(tmp_path, monkeypatch, override, window):
    async def scenario():
        (tmp_path / "MEMORY.md").write_text("v1\nMemory")
        (tmp_path / "memory_summary.md").write_text("v1\nIndex")
        captured = []
        original = LangGraphRuntime.create

        def create(**kwargs):
            captured.append(kwargs["settings"].main_context_limits)
            return original(**kwargs)

        monkeypatch.setattr(LangGraphRuntime, "create", create)
        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            context_window_tokens=window,
            model_context_window_override=override,
        )
        selected = settings.model_context_info(settings.resolved_memory_consolidation_model)
        requests = []

        class TrackedModel(Model):
            async def stream(self, request):
                requests.append(request)
                async for event in super().stream(request):
                    yield event

        async def run():
            await agent.run_agent(
                settings=settings,
                model=TrackedModel(),
                sampled=workspace.capture(tmp_path),
                instructions="Consolidate",
                workspace_diff="diff",
            )

        if window == 3000 and override is None:
            # Full worker policy/schema/permissions no longer fit in 2850
            # effective tokens. The child must honor, not silently enlarge, it.
            with pytest.raises(ValueError, match="prepared context exceeds model window"):
                await run()
            assert requests == []
        else:
            await run()
            assert len(requests) == 1
        assert captured[0].raw_tokens == selected.resolved_context_window
        assert captured[0].effective_percent == selected.effective_context_window_percent

    asyncio.run(scenario())
