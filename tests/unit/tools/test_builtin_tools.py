import asyncio
import base64
import os
import re
import signal
import stat
from pathlib import Path

import pytest

from corki.protocol.ids import new_tool_call_id
from corki.protocol.tools import ToolCall
from corki.tools.base import ToolContext
from corki.tools.builtin import (
    ApplyPatchTool,
    ExecCommandTool,
    ProcessManager,
    UpdatePlanTool,
    ViewImageTool,
    WriteStdinTool,
)
from corki.tools.builtin import patch as patch_module
from corki.tools.builtin import process as process_module
from corki.tools.builtin.process import _ProcessSession


def call(name: str, arguments: dict[str, object]) -> ToolCall:
    return ToolCall(new_tool_call_id(), name, arguments)


def test_exec_command_and_write_stdin_resume_process(tmp_path: Path) -> None:
    async def scenario() -> None:
        processes = ProcessManager()
        try:
            execute = ExecCommandTool(processes, default_yield_seconds=0.02, timeout_seconds=2)
            write = WriteStdinTool(processes, default_yield_seconds=0.3)

            first = await execute.execute(
                call(
                    "exec_command",
                    {"cmd": "printf start; sleep 0.1; printf end", "login": False},
                ),
                ToolContext(tmp_path),
            )
            match = re.search(r"session ID ([0-9a-f-]+)", first.content)
            assert match is not None

            second = await write.execute(
                call("write_stdin", {"session_id": match.group(1)}),
                ToolContext(tmp_path),
            )
            assert "exited with code 0" in second.content
            assert "start" in f"{first.content}{second.content}"
            assert "end" in second.content
        finally:
            await processes.terminate_all()

    asyncio.run(scenario())


def test_exec_command_supports_a_real_pseudo_terminal(tmp_path: Path) -> None:
    async def scenario() -> None:
        processes = ProcessManager()
        observation = await processes.execute(
            "test -t 0 && printf tty-ok",
            cwd=tmp_path,
            yield_seconds=1,
            timeout_seconds=2,
            tty=True,
            login=False,
        )
        assert observation.exit_code == 0
        assert "tty-ok" in observation.output
        await processes.terminate_all()

    asyncio.run(scenario())


def test_process_output_budget_keeps_exact_tail_for_oversized_chunk() -> None:
    session = _ProcessSession("test", None, 5)  # type: ignore[arg-type]

    session.append(b"abcdefgh")

    output = session.take_output()
    assert "3 earlier output bytes omitted" in output
    assert output.endswith("defgh")


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups only")
def test_process_termination_escalates_to_the_entire_process_group(monkeypatch) -> None:
    class StubbornProcess:
        pid = 4242

        def __init__(self) -> None:
            self.returncode: int | None = None
            self.reaped = asyncio.Event()
            self.direct_kill_called = False

        async def wait(self) -> int:
            await self.reaped.wait()
            assert self.returncode is not None
            return self.returncode

        def kill(self) -> None:
            self.direct_kill_called = True

    process = StubbornProcess()
    signals: list[signal.Signals] = []

    def kill_group(pid: int, sent: signal.Signals) -> None:
        assert pid == process.pid
        signals.append(sent)
        if sent == signal.SIGKILL:
            process.returncode = -int(sent)
            process.reaped.set()

    monkeypatch.setattr(process_module.os, "killpg", kill_group)
    monkeypatch.setattr(process_module, "_TERMINATE_GRACE_SECONDS", 0.001)

    asyncio.run(ProcessManager._terminate(process))  # type: ignore[arg-type]

    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert not process.direct_kill_called


def test_apply_patch_add_update_move_and_delete(tmp_path: Path) -> None:
    async def scenario() -> None:
        tool = ApplyPatchTool()
        context = ToolContext(tmp_path)
        added = await tool.execute(
            call(
                "apply_patch",
                {
                    "patch": """*** Begin Patch
*** Add File: hello.txt
+first
+second
*** End Patch"""
                },
            ),
            context,
        )
        assert "added hello.txt" in added.content

        await tool.execute(
            call(
                "apply_patch",
                {
                    "patch": """*** Begin Patch
*** Update File: hello.txt
*** Move to: nested/greeting.txt
@@
 first
-second
+changed
*** End Patch"""
                },
            ),
            context,
        )
        assert (tmp_path / "nested/greeting.txt").read_text() == "first\nchanged\n"
        assert not (tmp_path / "hello.txt").exists()

        await tool.execute(
            call(
                "apply_patch",
                {
                    "patch": """*** Begin Patch
*** Delete File: nested/greeting.txt
*** End Patch"""
                },
            ),
            context,
        )
        assert not (tmp_path / "nested/greeting.txt").exists()

    asyncio.run(scenario())


def test_apply_patch_preflight_prevents_partial_content_change(tmp_path: Path) -> None:
    async def scenario() -> None:
        tool = ApplyPatchTool()
        try:
            await tool.execute(
                call(
                    "apply_patch",
                    {
                        "patch": """*** Begin Patch
*** Add File: should-not-exist.txt
+temporary
*** Update File: missing.txt
@@
-missing
+changed
*** End Patch"""
                    },
                ),
                ToolContext(tmp_path),
            )
        except ValueError as exc:
            assert "missing file" in str(exc)
        else:
            raise AssertionError("invalid patch unexpectedly succeeded")
        assert not (tmp_path / "should-not-exist.txt").exists()

    asyncio.run(scenario())


def test_apply_patch_rolls_back_all_files_if_commit_fails(monkeypatch, tmp_path: Path) -> None:
    first, second = tmp_path / "first.txt", tmp_path / "second.txt"
    first.write_text("first\n")
    second.write_text("second\n")
    first.chmod(0o744)
    real_replace = patch_module.os.replace
    calls = 0

    def fail_second_replace(source, destination) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected commit failure")
        real_replace(source, destination)

    monkeypatch.setattr(patch_module.os, "replace", fail_second_replace)
    patch = """*** Begin Patch
*** Update File: first.txt
@@
-first
+changed-first
*** Update File: second.txt
@@
-second
+changed-second
*** End Patch"""

    with pytest.raises(OSError, match="injected"):
        patch_module._apply_operations(tmp_path, patch_module._parse_patch(patch))

    assert first.read_text() == "first\n"
    assert second.read_text() == "second\n"
    assert stat.S_IMODE(first.stat().st_mode) == 0o744


def test_update_plan_enforces_single_active_step(tmp_path: Path) -> None:
    async def scenario() -> None:
        tool = UpdatePlanTool()
        result = await tool.execute(
            call(
                "update_plan",
                {
                    "plan": [
                        {"step": "inspect", "status": "completed"},
                        {"step": "implement", "status": "in_progress"},
                    ]
                },
            ),
            ToolContext(tmp_path),
        )
        assert result.state_update.plan is not None
        assert result.state_update.plan[1]["status"] == "in_progress"

    asyncio.run(scenario())


def test_view_image_returns_multimodal_attachment(tmp_path: Path) -> None:
    # One transparent 1x1 PNG.
    image = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF"
        "gAI/ScL7WQAAAABJRU5ErkJggg=="
    )
    path = tmp_path / "pixel.png"
    path.write_bytes(image)

    async def scenario() -> None:
        result = await ViewImageTool().execute(
            call("view_image", {"path": "pixel.png", "detail": "original"}),
            ToolContext(tmp_path),
        )
        assert result.attachments[0].data_url.startswith("data:image/png;base64,")
        assert result.attachments[0].detail == "original"

    asyncio.run(scenario())
