import asyncio
import json
import shlex
import shutil
import sqlite3
import sys

import pytest

from corki.code_mode.service import CodeModeService
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.memory import git_baseline, workspace
from corki.memory.artifacts import write_baseline
from corki.models import ModelCompleted, ModelItemCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    ToolCallItem,
    ToolResultItem,
    new_step_id,
)
from corki.protocol.session_source import SessionSource
from corki.protocol.tools import ToolCall


@pytest.mark.parametrize("bundled", [False, True])
def test_memory_worker_uses_real_tools_before_owner_fenced_publication(tmp_path, bundled):
    if bundled and not CodeModeService.available():
        pytest.skip("selected consolidation model requires corki[code-mode]")

    async def scenario():
        root = tmp_path / "memories"
        source = root / "extensions/team/evidence.txt"
        source.parent.mkdir(parents=True)
        source.write_text("SOURCE_EVIDENCE\n")
        calls = []

        class Main:
            async def stream(self, request):
                yield ModelCompleted(
                    (AssistantMessageItem("main ready", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class Memory:
            closed = 0

            async def stream(self, request):
                calls.append(request)
                assert request.model == ("gpt-5.6-terra" if bundled else "fixture")
                names = {spec.name for spec in request.tools}
                assert ("exec_command" in names) is (not bundled)
                assert ("exec" in names) is bundled
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(calls) == 1:
                    call = ToolCall(
                        new_tool_call_id(),
                        "exec_command",
                        {"cmd": "cat extensions/team/evidence.txt", "login": False},
                    )
                    if bundled:
                        call = ToolCall(
                            call.id,
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments="text(await tools.exec_command("
                            + json.dumps(call.arguments)
                            + "));",
                        )
                    yield ModelCompleted((ToolCallItem(call, turn, step),))
                else:
                    result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    assert "SOURCE_EVIDENCE" in result.content and not result.is_error
                    value = {
                        "memory": "Verified source",
                        "memory_summary": "Verified source",
                        "skills": [],
                    }
                    yield ModelCompleted((AssistantMessageItem(json.dumps(value), turn, step),))

            async def aclose(self):
                self.closed += 1

        model = Memory()
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                memories_consolidation_model="gpt-5.6-terra" if bundled else "fixture",
            ),
            database_path=tmp_path / "sessions.db",
            memory_root=root,
            model=Main(),
            memory_model=model,
        )
        try:
            events = [e async for e in runtime.stream("work")]
            assert isinstance(events[-1], TurnCompleted)
            report = await runtime._memory_service.wait()
            assert report.consolidated and not report.failed, runtime._memory_service.warnings
            assert len(calls) == 2 and model.closed == 0
            assert "Verified source" in (root / "MEMORY.md").read_text()
            parent = await runtime._repository.load_items(runtime.thread_id)
            assert not any(isinstance(i, ToolCallItem) for i in parent)
        finally:
            await runtime.aclose()
            assert model.closed == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("route", ["completed", "streaming", "nested"])
def test_file_editing_worker_repairs_tool_error_verifies_and_recalls(
    tmp_path, monkeypatch, route, memory_worker_state_dirs
):
    async def scenario():
        root = tmp_path / "memories"
        source = root / "extensions/team/source.txt"
        source.parent.mkdir(parents=True)
        source.write_text("SOURCE_EVIDENCE")
        obsolete = root / "skills/old/SKILL.md"
        obsolete.parent.mkdir(parents=True)
        obsolete.write_text("obsolete skill")
        child_copies, main_requests, requests = memory_worker_state_dirs, [], []
        original_close = LangGraphRuntime.aclose
        patch = (
            "*** Begin Patch\n*** Add File: MEMORY.md\n+v1\n+SOURCE_EVIDENCE "
            "AKIAABCDEFGHIJKLMNOP\n*** Add File: memory_summary.md\n+v1\n+RECALL_VERIFIED\n"
            "*** Add File: skills/verify/SKILL.md\n+---\n+name: verify\n"
            "+description: Verify the source\n+---\n+Read SOURCE_EVIDENCE.\n"
            "*** Add File: skills/verify/scripts/check.sh\n+echo SOURCE_EVIDENCE\n"
            "*** Delete File: skills/old/SKILL.md\n*** End Patch"
        )
        verify = (
            "from pathlib import Path; "
            "assert 'SOURCE_EVIDENCE' in Path('MEMORY.md').read_text(); "
            "assert Path('memory_summary.md').read_text().startswith('v1\\n'); "
            "Path('skills/verify/scripts/check.sh').chmod(0o700); "
            "Path('skills/verify/asset.bin').write_bytes(bytes([255, 0, 254])); "
            "print('VERIFIED')"
        )
        operations = [
            ("unavailable_tool", {}),
            ("exec_command", {"cmd": "cat extensions/team/source.txt", "login": False}),
            ("apply_patch", {"patch": patch}),
            ("exec_command", {"cmd": shlex.join([sys.executable, "-c", verify]), "login": False}),
        ]

        class Main:
            async def stream(self, request):
                main_requests.append(request)
                yield ModelCompleted(
                    (AssistantMessageItem("main", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class Memory:
            async def stream(self, request):
                requests.append(request)
                index = len(requests) - 1
                assert request.output_schema is None and request.reasoning_effort == "medium"
                assert (root / "MEMORY.md").exists() == (index >= 3)
                if index:
                    result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    if index == 1:
                        assert result.is_error or "unavailable_tool" in result.content
                    elif index == 2:
                        assert "SOURCE_EVIDENCE" in result.content
                    elif index == 4:
                        assert "VERIFIED" in result.content
                turn, step = request.items[-1].turn_id, new_step_id()
                if index == len(operations):
                    yield ModelCompleted((AssistantMessageItem("verified", turn, step),))
                    return
                name, args = operations[index]
                if route == "nested":
                    call = ToolCall(
                        new_tool_call_id(),
                        "exec",
                        None,
                        input_kind="freeform",
                        raw_arguments=f"text(await tools.{name}({json.dumps(args)}));",
                    )
                else:
                    call = ToolCall(new_tool_call_id(), name, args)
                item = ToolCallItem(call, turn, step)
                if route == "streaming":
                    yield ModelItemCompleted(item)
                yield ModelCompleted((item,))

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                tool_mode="code_mode" if route == "nested" else "direct",
                memories_consolidation_model="fixture",
            ),
            database_path=tmp_path / "sessions.db",
            memory_root=root,
            model=Main(),
            memory_model=Memory(),
        )

        async def close(child):
            if child is not runtime:
                assert (root / "MEMORY.md").exists()
                assert child._memory_service is None
                assert await child._repository.load_thread_source(
                    child.thread_id
                ) == SessionSource.internal("memory_consolidation")
                assert not child._settings.mcp_servers and not child._settings.plugin_dirs
                assert child._settings.working_directory == root
            await original_close(child)

        monkeypatch.setattr(LangGraphRuntime, "aclose", close)
        try:
            assert isinstance([e async for e in runtime.stream("work")][-1], TurnCompleted)
            report = await runtime._memory_service.wait()
            assert report.consolidated and not report.failed, runtime._memory_service.warnings
            assert len(requests) == 5 and child_copies
            assert all(not path.exists() for path in child_copies)
            memory = (root / "MEMORY.md").read_text()
            # Native file validation does not rewrite bytes emitted by the worker.
            assert "SOURCE_EVIDENCE" in memory and "AKIAABCDEFGHIJKLMNOP" in memory
            assert source.read_text() == "SOURCE_EVIDENCE" and not obsolete.exists()
            assert (root / "skills/verify/asset.bin").read_bytes() == bytes([255, 0, 254])
            assert (root / "skills/verify/scripts/check.sh").stat().st_mode & 0o111
            prefix = await runtime._repository.load_items(runtime.thread_id)
            assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
            assert isinstance([e async for e in runtime.stream("recall")][-1], TurnCompleted)
            assert (await runtime._repository.load_items(runtime.thread_id))[
                : len(prefix)
            ] == prefix
            assert any(
                isinstance(i, ContextItem)
                and i.key == "memory.instructions"
                and "RECALL_VERIFIED" in (i.snapshot_content or i.content)
                for i in main_requests[-1].items
            )
            assert not any(
                isinstance(i, ToolCallItem)
                for i in await runtime._repository.load_items(runtime.thread_id)
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("stop", ["completed", "cancelled", "heartbeat"])
def test_unconfirmed_child_shutdown_keeps_lease_even_during_stop(
    tmp_path, monkeypatch, stop, memory_worker_state_dirs
):
    async def scenario():
        closing, release = asyncio.Event(), asyncio.Event()
        root, database = tmp_path / "memories", tmp_path / "sessions.db"
        children = memory_worker_state_dirs

        class Model:
            async def stream(self, request):
                text = json.dumps({"memory": "new", "memory_summary": "new", "skills": []})
                yield ModelCompleted(
                    (AssistantMessageItem(text, request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, memories_enabled=True
            ),
            database_path=database,
            memory_root=root,
            model=Model(),
            memory_model=Model(),
        )
        original_close = LangGraphRuntime.aclose

        async def close(child):
            if child is runtime:
                return await original_close(child)
            assert child._settings.working_directory == root
            closing.set()
            await release.wait()
            await original_close(child)  # Fixture owns/reaps resources before injecting failure.
            raise OSError("shutdown acknowledgement lost")

        monkeypatch.setattr(LangGraphRuntime, "aclose", close)
        if stop == "heartbeat":

            async def heartbeat(claim):
                await closing.wait()
                raise OSError("heartbeat database failure")

            monkeypatch.setattr(runtime._memory_service, "_heartbeat", heartbeat)
        try:
            assert isinstance([e async for e in runtime.stream("main")][-1], TurnCompleted)
            await asyncio.wait_for(closing.wait(), 3)
            task = runtime._memory_service._task
            if stop == "cancelled":
                task.cancel()
            # Yield so the heartbeat/cancel branch can start joining the child.
            await asyncio.sleep(0)
            release.set()
            if stop == "cancelled":
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                assert (await task).failed == 1
            with sqlite3.connect(database) as db:
                status, token = db.execute(
                    "SELECT status,ownership_token FROM memory_jobs WHERE job_key='global'"
                ).fetchone()
            assert status == "running" and token
            assert "MEMORY.md" not in git_baseline.read(root)
            assert not (root / "MEMORY.md").exists()
            assert children and all(path.exists() for path in children)
            assert any("shutdown" in w for w in runtime._memory_service.warnings)
        finally:
            release.set()
            await original_close(runtime)
            for path in children:
                shutil.rmtree(path)  # Only fixture-owned copies; the real child was closed above.

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure",
    [
        "model",
        "budget",
        "cancelled",
        "lost_owner",
        "source_mutation",
        "invalid_summary",
        "baseline_commit",
        "database_success",
        "database_success_cold",
    ],
)
def test_file_editing_child_preserves_side_effects_without_false_success(
    tmp_path, monkeypatch, failure, memory_worker_state_dirs
):
    async def scenario():
        root, database = tmp_path / "memories", tmp_path / "sessions.db"
        root.mkdir()
        for name in ("MEMORY.md", "memory_summary.md"):
            (root / name).write_text("v1\nold\n")
        write_baseline(root, workspace.digest(workspace.capture(root), outputs=False))
        baseline = workspace.read_previous(root)
        source = root / "extensions/team/source.txt"
        source.parent.mkdir(parents=True)
        source.write_text("source\n")
        entered = asyncio.Event()
        copies = memory_worker_state_dirs
        original_close = LangGraphRuntime.aclose

        class Main:
            async def stream(self, request):
                yield ModelCompleted(
                    (AssistantMessageItem("main", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class Memory:
            count = 0

            async def stream(self, request):
                self.count += 1
                turn, step = request.items[-1].turn_id, new_step_id()
                if self.count == 1:
                    header = "invalid" if failure == "invalid_summary" else "v1"
                    patch = (
                        "*** Begin Patch\n*** Update File: MEMORY.md\n@@\n-old\n+new\n"
                        "*** Update File: memory_summary.md\n@@\n-v1\n-old\n"
                        f"+{header}\n+new\n"
                    )
                    if failure == "source_mutation":
                        patch += (
                            "*** Update File: extensions/team/source.txt\n@@\n-source\n+changed\n"
                        )
                    patch += "*** End Patch"
                    call = ToolCall(new_tool_call_id(), "apply_patch", {"patch": patch})
                    yield ModelCompleted((ToolCallItem(call, turn, step),))
                    return
                result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                assert not result.is_error, result.content
                if failure == "model":
                    raise RuntimeError("injected post-edit model failure")
                if failure == "cancelled":
                    entered.set()
                    await asyncio.Event().wait()
                if failure == "lost_owner":
                    with sqlite3.connect(database) as db:
                        db.execute(
                            "UPDATE memory_jobs SET ownership_token='successor' "
                            "WHERE job_key='global'"
                        )
                if failure == "baseline_commit":

                    def fail_baseline(root):
                        raise OSError("injected baseline commit failure")

                    monkeypatch.setattr(git_baseline, "reset", fail_baseline)
                if failure.startswith("database_success"):
                    with sqlite3.connect(database) as db:
                        db.execute(
                            "CREATE TRIGGER reject_memory_success BEFORE UPDATE ON memory_jobs "
                            "WHEN NEW.job_key='global' AND NEW.status='succeeded' "
                            "BEGIN SELECT RAISE(ABORT, 'injected success transaction failure'); END"
                        )
                yield ModelCompleted((AssistantMessageItem("done", turn, step),))

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                max_steps=1 if failure == "budget" else 24,
                memories_consolidation_model="fixture",
            ),
            database_path=database,
            memory_root=root,
            model=Main(),
            memory_model=Memory(),
        )

        async def close(child):
            if child is not runtime:
                assert child._settings.working_directory == root
            await original_close(child)

        monkeypatch.setattr(LangGraphRuntime, "aclose", close)
        try:
            assert isinstance([e async for e in runtime.stream("work")][-1], TurnCompleted)
            if failure == "cancelled":
                await asyncio.wait_for(entered.wait(), 3)
                runtime._memory_service._task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await runtime._memory_service.wait()
            else:
                report = await runtime._memory_service.wait()
                if failure == "source_mutation":
                    assert report.consolidated and not report.failed
                else:
                    assert report.failed == 1 and not report.consolidated
            assert copies and all(not path.exists() for path in copies)
            # This budget expires before the first tool is admitted; other
            # branches fail after an acknowledged file edit and cannot undo it.
            body = "old" if failure == "budget" else "new"
            assert (root / "MEMORY.md").read_text() == "v1\n" + body + "\n", (
                runtime._memory_service.warnings
            )
            header = "invalid" if failure == "invalid_summary" else "v1"
            assert (root / "memory_summary.md").read_text() == header + "\n" + body + "\n"
            if failure == "source_mutation" or failure.startswith("database_success"):
                assert git_baseline.read(root) != baseline
                assert source.read_text() == (
                    "changed\n" if failure == "source_mutation" else "source\n"
                )
            else:
                assert git_baseline.read(root) == baseline
                assert source.read_text() == "source\n"
            if failure == "baseline_commit" or failure.startswith("database_success"):
                with sqlite3.connect(database) as db:
                    row = db.execute(
                        "SELECT status, completed_watermark, ownership_token FROM memory_jobs "
                        "WHERE job_key='global'"
                    ).fetchone()
                assert row == ("failed", 0, None)
            if failure.startswith("database_success"):
                published = git_baseline.read(root)
                assert runtime._memory_service._model.count == 2
                with sqlite3.connect(database) as db:
                    db.execute("DROP TRIGGER reject_memory_success")
                    # Advance only this fixture's retry eligibility, without
                    # changing the failed status or either watermark.
                    db.execute("UPDATE memory_jobs SET retry_at=0 WHERE job_key='global'")
                cold = failure == "database_success_cold"
                if cold:
                    await runtime.aclose()
                    runtime = await LangGraphRuntime.acreate(
                        settings=runtime._settings,
                        database_path=database,
                        memory_root=root,
                        thread_id=runtime.thread_id,
                        model=Main(),
                        memory_model=Memory(),
                    )
                assert isinstance([e async for e in runtime.stream("retry")][-1], TurnCompleted)
                retried = await runtime._memory_service.wait()
                assert retried.consolidation_skipped and not retried.failed
                assert not retried.consolidated
                assert git_baseline.read(root) == published
                assert (root / "MEMORY.md").read_text() == "v1\nnew\n"
                with sqlite3.connect(database) as db:
                    row = db.execute(
                        "SELECT status, completed_watermark=input_watermark, ownership_token "
                        "FROM memory_jobs WHERE job_key='global'"
                    ).fetchone()
                assert row == ("succeeded", 1, None)
                assert runtime._memory_service._model.count == (0 if cold else 2)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
