"""Expiry must reach the real consolidator before the unchanged-workspace gate."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from memory_evidence import inspect_worker_evidence

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.items import AssistantMessageItem, new_step_id


@pytest.mark.parametrize("failure", ["none", "model", "unlink"])
def test_expiry_reaches_runtime_diff_and_preserves_failed_publication(
    tmp_path, monkeypatch, caplog, failure
):
    async def scenario():
        clock = [datetime(2026, 4, 14, 6, tzinfo=UTC).timestamp()]
        monkeypatch.setattr("corki.memory.sqlite.time.time", lambda: clock[0])
        root = tmp_path / "memories"
        resources = root / "extensions/team/resources"
        resources.mkdir(parents=True)
        (resources.parent / "instructions.md").write_text("Team evidence")
        expiring = resources / "2026-04-07T12-00-00-cutoff.md"
        expiring.write_text("EXPIRING_FACT\n")
        second = resources / "2026-04-07T12-00-00-second.md"
        second.write_text("SECOND_FACT\n")
        requests = []
        unlink = Path.unlink

        def remove(path, *args, **kwargs):
            if failure == "unlink" and path == expiring:
                raise PermissionError("injected prune denial")
            return unlink(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", remove)

        class Main:
            async def stream(self, request):
                yield ModelCompleted(
                    (AssistantMessageItem("ready", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class Memory(Main):
            async def stream(self, request):
                payload = inspect_worker_evidence(request)
                requests.append(payload)
                if len(requests) > 1:
                    diff = payload["workspace_diff"]
                    assert "- D extensions/team/resources/2026-04-07T12-00-00-second.md" in diff
                    assert "-SECOND_FACT" in diff
                    assert not second.exists()
                    assert expiring.exists() == (failure == "unlink")
                    if failure != "unlink":
                        assert "-EXPIRING_FACT" in diff
                        assert "EXPIRING_FACT" not in payload["extension_sources"]
                if len(requests) == 2 and failure == "model":
                    raise RuntimeError("injected sampling failure")
                value = "OLD_PUBLICATION" if len(requests) == 1 else "NEW_PUBLICATION"
                yield ModelCompleted(
                    (
                        AssistantMessageItem(
                            json.dumps({"memory": value, "memory_summary": value, "skills": []}),
                            request.items[-1].turn_id,
                            new_step_id(),
                        ),
                    )
                )

        thread = None

        async def run():
            nonlocal thread
            runtime = LangGraphRuntime.create(
                settings=CorkiSettings(
                    working_directory=tmp_path, skills_enabled=False, memories_enabled=True
                ),
                database_path=tmp_path / "history.db",
                memory_root=root,
                model=Main(),
                memory_model=Memory(),
                thread_id=thread,
            )
            thread = runtime.thread_id
            try:
                assert isinstance([e async for e in runtime.stream("work")][-1], TurnCompleted)
                return await runtime._memory_service.wait()
            finally:
                await runtime.aclose()

        assert (await run()).consolidated
        from corki.memory import git_baseline

        baseline = git_baseline.read(root)
        assert expiring.exists() and second.exists()
        clock[0] += 6 * 3600  # Both success cooldown and exact seven-day filename cutoff.
        report = await run()
        assert not second.exists(), "expiry must run before the no-change baseline check"
        if failure == "model":
            assert report.failed == 1
            assert git_baseline.read(root) == baseline
            assert "OLD_PUBLICATION" in (root / "memory_summary.md").read_text()
            clock[0] += 6 * 3600
            report = await run()
        assert report.consolidated
        assert "NEW_PUBLICATION" in (root / "memory_summary.md").read_text()
        if failure == "unlink":
            assert "injected prune denial" in caplog.text
            assert expiring.exists()
        clock[0] += 6 * 3600
        assert (await run()).consolidation_skipped
        assert len(requests) == (3 if failure == "model" else 2)

    asyncio.run(scenario())
