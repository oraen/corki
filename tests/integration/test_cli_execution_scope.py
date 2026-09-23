"""Keyboard scope decisions reach real execution and remain command/session bound."""

import asyncio
import shlex
from io import StringIO

import pytest
import test_execution_approval_cancel as execution_fixture
from prompt_toolkit import PromptSession
from prompt_toolkit.history import DummyHistory
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console
from test_execution_approval_cancel import ApprovalModel, create_runtime, observe
from test_execution_rule_amendments import Model as RuleModel
from test_execution_rule_amendments import runtime_for as rule_runtime

from corki.cli.application import CorkiApplication
from corki.cli.approval_details import ApprovalDetails
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.protocol.events import TurnCancelled, TurnCompleted, TurnFailed, WarningEvent

compiler = execution_fixture.compiler


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("finish", ["decline", "cancel", "close"])
@pytest.mark.parametrize("operation", ["shell", "patch"])
def test_details_pager_keeps_runtime_approvals_pending_and_cleans_up(
    tmp_path, compiler, mode, finish, operation
):
    async def scenario(pipe):
        target = tmp_path / "must-not-run"
        model = ApprovalModel(
            mode,
            "mkdir " + shlex.quote(str(target)),
            count=2,
            settle=finish == "decline",
            patch=(
                f"*** Begin Patch\n*** Add File: {target}\n+"
                + "long patch content " * 2000
                + "PATCHREVIEWTAIL\n*** End Patch"
                if operation == "patch"
                else None
            ),
        )
        runtime = await create_runtime(tmp_path, compiler, model)
        settings = CorkiSettings(tmp_path)
        requests = []

        class UI(TerminalUI):
            async def read_elicitation(self, request):
                requests.append(request.request_id)
                return await super().read_elicitation(request)

        ui = UI(settings, tmp_path / "input-history", console=Console(file=StringIO()))
        ui._form_session = PromptSession(input=pipe, output=DummyOutput(), history=DummyHistory())
        ui._form_session.app.ttimeoutlen = 0.01
        original_layout = ui._form_session.layout.container
        ready = asyncio.Queue()
        prompt = ui._form_session.prompt_async

        async def started(*args, **kwargs):
            return await prompt(*args, **kwargs, pre_run=lambda: ready.put_nowait(True))

        ui._form_session.prompt_async = started
        CorkiApplication(settings, CorkiPaths.from_home(tmp_path / "home"), runtime, ui)
        task = asyncio.create_task(observe(runtime, "review concurrent commands"))
        try:
            async with asyncio.timeout(15):
                await ready.get()
                pipe.send_text("\x01")
                while not isinstance(ui._form_session.layout.current_control, ApprovalDetails):
                    await asyncio.sleep(0.01)
                pager = ui._form_session.layout.current_control
                assert str(target) in pager.content
                if operation == "patch":
                    assert "PATCHREVIEWTAIL" in pager.content
                    assert len(pager.content) > 12000
                    for width in (40, 100):
                        content = pager.create_content(width, 20)
                        rendered = [
                            "".join(fragment[1] for fragment in content.get_line(line))
                            for line in range(content.line_count)
                        ]
                        start = next(
                            line
                            for line, text in enumerate(rendered)
                            if text.startswith("  1 +long patch content")
                        )
                        body = [rendered[start][5:]]
                        for continuation in rendered[start + 1 :]:
                            if not continuation:
                                break
                            assert continuation.startswith("     ")
                            assert len(continuation) <= width
                            body.append(continuation[5:])
                        assert "".join(body) == "long patch content " * 2000 + "PATCHREVIEWTAIL"
                        assert "PATCHREVIEWTAIL" in "".join(
                            fragment[1]
                            for line in range(content.line_count)
                            for fragment in content.get_line(line)
                        )
                        assert "PATCHREVIEWTAIL" in "".join(
                            fragment[1]
                            for line in range(content.line_count)
                            for fragment in content.get_line(line)
                            if "fg:" in fragment[0]
                        )
                    size = ui._form_session.app.output.get_size()
                    pager.create_content(size.columns, size.rows)
                assert runtime._process_manager.approvals.router._pending
                assert not target.exists() and not task.done()
                pipe.send_text("y1\r\x1b[200~q\x03\x1b[201~\x1b[F")
                while pager.row != len(pager.lines) - 1:
                    await asyncio.sleep(0.01)
                assert pager.active and not target.exists() and not task.done()
                if finish == "close":
                    await runtime.aclose()
                else:
                    pipe.send_text("q")
                    while pager.active:
                        await asyncio.sleep(0.01)
                    pipe.send_text("n" if finish == "cancel" else "d")
                    if finish == "decline":
                        await ready.get()
                        pipe.send_text("\x01")
                        while not isinstance(
                            ui._form_session.layout.current_control, ApprovalDetails
                        ):
                            await asyncio.sleep(0.01)
                        second = ui._form_session.layout.current_control
                        assert second is not pager
                        assert str(target) in second.content
                        pipe.send_text("q")
                        while second.active:
                            await asyncio.sleep(0.01)
                        pipe.send_text("d")
                events = await task
            terminal = [
                e for e in events if isinstance(e, (TurnCompleted, TurnCancelled, TurnFailed))
            ]
            assert len(terminal) == 1
            assert isinstance(terminal[0], TurnCompleted if finish == "decline" else TurnCancelled)
            assert len(model.requests) == (2 if finish == "decline" else 1)
            assert len(requests) == len(set(requests))
            if finish == "decline":
                assert len(requests) == 2
            assert not target.exists()
            assert not runtime._process_manager.approvals._session
            assert not runtime._process_manager.approvals.router._pending
            assert not runtime._process_manager._sessions and not runtime._process_manager._starting
            assert ui._form_session.layout.container is original_layout
            assert not ui._form_session.app.renderer._in_alternate_screen
            assert not ui._form_session.app.full_screen
            assert ui._transcript.modal_depth == 0
            assert not ui._form_session.default_buffer.text
            assert not list(ui._form_session.history.get_strings())
            if mode == "code_mode_only":
                assert not runtime._code_mode.cells
        finally:
            await runtime.aclose()
            await asyncio.gather(task, return_exceptions=True)

    with create_pipe_input() as pipe:
        asyncio.run(scenario(pipe))


@pytest.mark.parametrize("scope", ["once", "session"])
@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("input_method", ["arrows", "letter", "number"])
def test_keyboard_scope_reprompts_for_changed_command_and_cold_session(
    tmp_path, compiler, scope, mode, input_method
):
    async def scenario(pipe):
        first = tmp_path / "first-operation"
        second = tmp_path / "changed-operation"
        command = "mkdir -p " + shlex.quote(str(first))
        model = ApprovalModel(mode, command)
        runtime = await create_runtime(tmp_path, compiler, model)
        prompts = []

        class UI(TerminalUI):
            async def read_elicitation(self, request):
                prompts.append(request)
                return await super().read_elicitation(request)

        settings = CorkiSettings(tmp_path)
        ui = UI(settings, tmp_path / "input-history", console=Console(file=StringIO()))
        ui._form_session = PromptSession(input=pipe, output=DummyOutput(), history=DummyHistory())
        prompt = ui._form_session.prompt_async

        async def decide(*args, **kwargs):
            def select():
                keys = {
                    "arrows": "\x1b[B\r" if scope == "session" else "\r",
                    "letter": "a" if scope == "session" else "y",
                    "number": "2" if scope == "session" else "1",
                }
                pipe.send_text(keys[input_method])

            return await prompt(*args, **kwargs, pre_run=select)

        ui._form_session.prompt_async = decide

        def bind():
            CorkiApplication(settings, CorkiPaths.from_home(tmp_path / "home"), runtime, ui)

        bind()
        try:
            async with asyncio.timeout(15):
                assert isinstance((await observe(runtime, "first"))[-1], TurnCompleted)
                assert first.is_dir() and len(prompts) == 1
                assert isinstance((await observe(runtime, "same command"))[-1], TurnCompleted)
                assert len(prompts) == (1 if scope == "session" else 2)
                model.command = "mkdir -p " + shlex.quote(str(second))
                assert isinstance((await observe(runtime, "different command"))[-1], TurnCompleted)
                assert second.is_dir()
                assert len(prompts) == (2 if scope == "session" else 3)
                assert len(model.requests) == 6
                thread = runtime.thread_id
                await runtime.aclose()
                runtime = await create_runtime(
                    tmp_path, compiler, ApprovalModel(mode, command), thread_id=thread
                )
                bind()
                assert isinstance((await observe(runtime, "cold session"))[-1], TurnCompleted)
                assert len(prompts) == (3 if scope == "session" else 4)
                assert not runtime._process_manager.approvals.router._pending
                assert not list(ui._form_session.history.get_strings())
                assert not list(ui._session.history.get_strings())
                assert ui._form_session.default_buffer.text == ""
        finally:
            await runtime.aclose()

    with create_pipe_input() as pipe:
        asyncio.run(scenario(pipe))


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("key", ["d", "n"])
def test_shortcut_decline_and_cancel_keep_execution_blocked(tmp_path, compiler, mode, key):
    async def scenario(pipe):
        target = tmp_path / "must-not-exist"
        model = ApprovalModel(mode, "mkdir " + shlex.quote(str(target)))
        runtime = await create_runtime(tmp_path, compiler, model)
        settings = CorkiSettings(tmp_path)
        ui = TerminalUI(settings, tmp_path / "input-history", console=Console(file=StringIO()))
        ui._form_session = PromptSession(input=pipe, output=DummyOutput(), history=DummyHistory())
        prompt = ui._form_session.prompt_async

        async def decide(*args, **kwargs):
            return await prompt(*args, **kwargs, pre_run=lambda: pipe.send_text(key))

        ui._form_session.prompt_async = decide
        CorkiApplication(settings, CorkiPaths.from_home(tmp_path / "home"), runtime, ui)
        try:
            async with asyncio.timeout(15):
                events = await observe(runtime, "review this command")
            terminal = [
                event
                for event in events
                if isinstance(event, (TurnCancelled, TurnCompleted, TurnFailed))
            ]
            assert len(terminal) == 1
            assert isinstance(terminal[0], TurnCancelled if key == "n" else TurnCompleted)
            assert not target.exists()
            assert len(model.requests) == (1 if key == "n" else 2)
            assert not runtime._process_manager.approvals._session
            assert not runtime._process_manager.approvals.router._pending
            assert await runtime._repository.latest_running_turn(runtime.thread_id) is None
            if mode == "code_mode_only":
                assert ("after_review" in runtime._code_mode.stored) is (key == "d")
                assert not runtime._code_mode.cells
            assert ui._form_session.default_buffer.text == ""
            assert not list(ui._form_session.history.get_strings())
            assert not list(ui._session.history.get_strings())
        finally:
            await runtime.aclose()

    with create_pipe_input() as pipe:
        asyncio.run(scenario(pipe))


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("save_fails", [False, True])
@pytest.mark.parametrize("input_method", ["arrows", "letter", "number"])
def test_keyboard_rule_scope_publishes_only_after_successful_save(
    tmp_path, compiler, mode, save_fails, input_method
):
    async def scenario(pipe):
        home = tmp_path / "home"
        home.mkdir()
        policy = home / "rules" / "default.rules"
        if save_fails:
            policy.mkdir(parents=True)
        first, second, cold = (tmp_path / name for name in ("first", "second", "cold"))
        model = RuleModel(
            mode,
            [
                {
                    "cmd": "touch " + shlex.quote(str(first)),
                    "sandbox_permissions": "require_escalated",
                    "prefix_rule": ["touch"],
                },
                {"cmd": "touch " + shlex.quote(str(second))},
            ],
        )
        runtime = await rule_runtime(tmp_path, compiler, model)
        prompts = []

        class UI(TerminalUI):
            async def read_elicitation(self, request):
                prompts.append(request)
                assert request.params["_meta"]["execpolicy_amendment"] == ["touch"]
                assert request.params["requestedSchema"]["properties"]["scope"]["enum"] == [
                    "once",
                    "session",
                    "rule",
                ]
                assert not first.exists() and not policy.is_file()
                return await super().read_elicitation(request)

        settings = CorkiSettings(tmp_path)
        ui = UI(settings, tmp_path / "input-history", console=Console(file=StringIO()))
        ui._form_session = PromptSession(input=pipe, output=DummyOutput(), history=DummyHistory())
        prompt = ui._form_session.prompt_async

        async def decide(*args, **kwargs):
            return await prompt(
                *args,
                **kwargs,
                pre_run=lambda: pipe.send_text(
                    {"arrows": "\x1b[B\x1b[B\r", "letter": "p", "number": "3"}[input_method]
                ),
            )

        ui._form_session.prompt_async = decide
        CorkiApplication(settings, CorkiPaths.from_home(home), runtime, ui)
        try:
            async with asyncio.timeout(15):
                events = await observe(runtime, "save the reviewed prefix")
                assert isinstance(events[-1], TurnCompleted)
                assert len(prompts) == 1 and first.is_file()
                assert second.exists() is not save_fails
                assert not runtime._process_manager.approvals._session
                warnings = [event.message for event in events if isinstance(event, WarningEvent)]
                assert (
                    any("Failed to apply execpolicy amendment" in w for w in warnings) == save_fails
                )
                if save_fails:
                    assert not runtime._process_manager.approvals.rules.prefixes
                else:
                    assert (
                        policy.read_text() == 'prefix_rule(pattern=["touch"], decision="allow")\n'
                    )
                    assert runtime._process_manager.approvals.rules.prefixes == (("touch",),)
                assert not list(ui._form_session.history.get_strings())
                assert not list(ui._session.history.get_strings())
        finally:
            await runtime.aclose()
        restored = await rule_runtime(
            tmp_path,
            compiler,
            RuleModel(
                mode,
                [
                    {"cmd": "touch " + shlex.quote(str(cold))},
                ],
            ),
        )
        try:
            async with asyncio.timeout(15):
                assert isinstance((await observe(restored, "cold rule load"))[-1], TurnCompleted)
                assert cold.exists() is not save_fails
                assert not restored._process_manager.approvals.router._pending
                if not save_fails:
                    assert (
                        policy.read_text() == 'prefix_rule(pattern=["touch"], decision="allow")\n'
                    )
        finally:
            await restored.aclose()

    with create_pipe_input() as pipe:
        asyncio.run(scenario(pipe))
