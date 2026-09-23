"""A real Runtime must give consolidation the deleted source, not just new hashes."""

import asyncio
import base64
import json
import sqlite3
import time

import pytest
from memory_evidence import inspect_worker_evidence

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.memory import artifacts, git_baseline
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.items import AssistantMessageItem, ContextItem, new_step_id


@pytest.mark.parametrize("change", ["delete", "modify"])
@pytest.mark.parametrize("failure", ["none", "model", "lost_owner", "summary_write", "skill_write"])
def test_runtime_consolidation_receives_named_change_and_cold_recall(
    tmp_path, monkeypatch, change, failure
):
    async def scenario():
        clock = [time.time()]
        monkeypatch.setattr("corki.memory.sqlite.time.time", lambda: clock[0])
        root, database = tmp_path / "memories", tmp_path / "history.db"
        note = root / "extensions/team/resource.md"
        note.parent.mkdir(parents=True)
        note.write_text("OLD_FACT\n", encoding="utf-8")
        (note.parent / "instructions.md").write_text("Team reference, not an explicit user update.")
        memories, mains = [], []
        write = artifacts._atomic_write

        def fail_summary_write(path, content):
            if (
                failure == "summary_write"
                and len(memories) == 2
                and path.name == "memory_summary.md"
            ):
                raise OSError("injected summary publication failure")
            if failure == "skill_write" and len(memories) == 2 and path.name == "SKILL.md":
                raise OSError("injected skill publication failure")
            write(path, content)

        monkeypatch.setattr(artifacts, "_atomic_write", fail_summary_write)

        class Main:
            async def stream(self, request):
                mains.append(request)
                yield ModelCompleted(
                    (AssistantMessageItem("ready", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class Memory:
            async def stream(self, request):
                payload = inspect_worker_evidence(request)
                memories.append(payload)
                if len(memories) >= 2:
                    diff = payload["workspace_diff"]
                    assert (
                        f"- {'D' if change == 'delete' else 'M'} extensions/team/resource.md"
                        in diff
                    )
                    assert "-OLD_FACT" in diff
                    assert "Team reference" in payload["extension_sources"]
                    assert "OLD_FACT" not in payload["ad_hoc_notes"]
                    assert "NEW_FACT" in diff if change == "modify" else "NEW_FACT" not in diff
                if len(memories) == 2 and failure == "model":
                    raise RuntimeError("injected consolidation failure")
                if len(memories) == 2 and failure == "lost_owner":
                    with sqlite3.connect(database) as db:
                        db.execute(
                            "UPDATE memory_jobs SET ownership_token='new-owner' "
                            "WHERE job_key='global'"
                        )
                text = "OLD_FACT" if len(memories) == 1 else "CURRENT_ONLY"
                skills = (
                    [
                        {
                            "name": "old-skill" if len(memories) == 1 else "new-skill",
                            "description": "Generated memory procedure",
                            "content": text,
                        }
                    ]
                    if failure == "skill_write"
                    else []
                )
                yield ModelCompleted(
                    (
                        AssistantMessageItem(
                            json.dumps({"memory": text, "memory_summary": text, "skills": skills}),
                            request.items[-1].turn_id,
                            new_step_id(),
                        ),
                    )
                )

            async def aclose(self):
                pass

        thread = None

        async def run(*, new_window=False):
            nonlocal thread
            runtime = await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    working_directory=tmp_path, skills_enabled=False, memories_enabled=True
                ),
                database_path=database,
                memory_root=root,
                model=Main(),
                memory_model=Memory(),
                thread_id=thread,
            )
            thread = runtime.thread_id
            try:
                events = [e async for e in runtime.stream("work")]
                assert isinstance(events[-1], TurnCompleted)
                report = await runtime._memory_service.wait()
                if new_window:
                    prefix = await runtime._repository.load_items(thread)
                    assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
                    assert isinstance(
                        [e async for e in runtime.stream("recall")][-1], TurnCompleted
                    )
                    assert (await runtime._repository.load_items(thread))[: len(prefix)] == prefix
                    await runtime._memory_service.wait()
                return report
            finally:
                await runtime.aclose()

        assert (await run()).consolidated
        old_baseline = git_baseline.read(root)
        with sqlite3.connect(database) as db:
            old_watermarks = db.execute(
                "SELECT completed_watermark, input_watermark FROM memory_jobs "
                "WHERE job_key='global'"
            ).fetchone()
        if change == "delete":
            note.unlink()
        else:
            note.write_text("NEW_FACT\n", encoding="utf-8")
        clock[0] += 6 * 3600
        if failure != "none":
            assert (await run()).failed == 1
            assert git_baseline.read(root) == old_baseline
            expected_summary = "CURRENT_ONLY" if failure == "skill_write" else "OLD_FACT"
            assert expected_summary in (root / "memory_summary.md").read_text()
            if failure in {"summary_write", "skill_write"}:
                # The detail file was replaced before the summary failed.
                # Neither rollback nor all-files atomic publication is promised.
                assert "CURRENT_ONLY" in (root / "MEMORY.md").read_text()
                with sqlite3.connect(database) as db:
                    status, completed, pending = db.execute(
                        "SELECT status, completed_watermark, input_watermark FROM memory_jobs "
                        "WHERE job_key='global'"
                    ).fetchone()
                # Extension-only changes need not advance stage-one watermarks.
                assert status == "failed" and (completed, pending) == old_watermarks
            if failure == "skill_write":
                assert not (root / "skills/old-skill").exists()
                assert not (root / "skills/new-skill/SKILL.md").exists()
            clock[0] += 6 * 3600
        assert (await run()).consolidated
        assert "CURRENT_ONLY" in (root / "memory_summary.md").read_text()
        if failure == "skill_write":
            assert "CURRENT_ONLY" in (root / "skills/new-skill/SKILL.md").read_text()
            assert not (root / "skills/old-skill").exists()
        assert git_baseline.read(root) != old_baseline
        clock[0] += 6 * 3600
        assert (await run(new_window=True)).consolidation_skipped
        assert len(memories) == (2 if failure == "none" else 3)
        context = next(
            i
            for i in reversed(mains[-1].items)
            if isinstance(i, ContextItem) and i.key == "memory.instructions"
        )
        current = (
            context.snapshot_content if context.snapshot_content is not None else context.content
        )
        assert "CURRENT_ONLY" in current and "OLD_FACT" not in current
        baseline = git_baseline.read(root)
        assert all(
            b"OLD_FACT" not in base64.b64decode(entry["content"]) for entry in baseline.values()
        )

    asyncio.run(scenario())
