import asyncio

import pytest
from test_thread_settings_update import Model, make_runtime, settings

from corki.cli.application import CorkiApplication
from corki.cli.commands import CommandAction, CommandDispatcher
from corki.config import CorkiPaths
from corki.protocol.items import ContextItem, UserMessageItem


@pytest.mark.parametrize("fail", [False, True])
def test_plan_command_publishes_before_inline_input_and_retains_failed_input(tmp_path, fail):
    async def scenario():
        configured = settings(tmp_path, realtime_enabled=False)
        model = Model()
        runtime = await make_runtime(tmp_path, model, configured=configured)
        modes = []
        messages = iter(["/plan", "/plan Review Foo\nKeepCase"])

        class UI:
            async def read_message(self):
                try:
                    return next(messages)
                except StopIteration:
                    raise EOFError from None

            async def append_assistant_delta_live(self, text):
                pass

            def set_collaboration_mode(self, mode):
                modes.append(mode)

            def __getattr__(self, name):
                if name.startswith(("show_", "begin_", "end_", "set_")):
                    return lambda *args, **kwargs: None
                raise AttributeError(name)

        if fail:

            async def fail_update(**kwargs):
                raise OSError("fixture settings write failed")

            runtime.update_thread_settings = fail_update
        paths = CorkiPaths.from_home(tmp_path / "home")
        app = CorkiApplication(configured, paths, runtime, UI())
        await app.run()
        if fail:
            assert not model.requests and not modes
            assert "/plan Review Foo\nKeepCase" in app._pending_messages
            assert not app._queue_autosend
            assert runtime.thread_settings.collaboration_mode == "default"
        else:
            assert len(model.requests) == 1
            request = model.requests[0]
            assert request.reasoning_effort == "medium"
            assert [i.content for i in request.items if isinstance(i, UserMessageItem)] == [
                "Review Foo\nKeepCase"
            ]
            assert any(
                isinstance(i, ContextItem) and "Plan Mode" in i.content for i in request.items
            )
            assert modes == ["plan", "plan"]
            assert app._default_collaboration_settings.reasoning_effort == "low"

    asyncio.run(scenario())


def test_plan_parser_preserves_inline_case_and_does_not_invent_off_toggle(tmp_path):
    commands = CommandDispatcher(settings(tmp_path), CorkiPaths.from_home(tmp_path / "home"))
    result = commands.dispatch("/plan off")
    assert result.action is CommandAction.PLAN and result.input_text == "off"
    assert commands.dispatch("/planet").action is CommandAction.NONE


@pytest.mark.parametrize("commit_fails", [False, True])
def test_cancelled_plan_publication_retains_input_and_reflects_joined_commit(
    tmp_path, monkeypatch, commit_fails
):
    async def scenario():
        configured = settings(tmp_path, realtime_enabled=False)
        model = Model()
        runtime = await make_runtime(tmp_path, model, configured=configured)
        await runtime._ensure_ready()
        before = runtime.thread_settings
        entered, release = asyncio.Event(), asyncio.Event()
        save = runtime._repository.save_thread_model_settings

        async def held(*args):
            entered.set()
            await release.wait()
            if commit_fails:
                raise OSError("fixture rejected commit")
            await save(*args)

        monkeypatch.setattr(runtime._repository, "save_thread_model_settings", held)
        modes, notices = [], []

        class UI:
            reads = 0

            async def read_message(self):
                self.reads += 1
                if self.reads == 1:
                    return "/plan Review Foo\nKeepCase"
                raise EOFError

            def set_collaboration_mode(self, mode):
                modes.append(mode)

            def show_notice(self, text):
                notices.append(text)

            def __getattr__(self, name):
                if name.startswith(("show_", "begin_", "end_", "set_")):
                    return lambda *args, **kwargs: None
                raise AttributeError(name)

        app = CorkiApplication(configured, CorkiPaths.from_home(tmp_path / "home"), runtime, UI())
        task = asyncio.create_task(app.run())
        try:
            await asyncio.wait_for(entered.wait(), 3)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            assert runtime.thread_settings == before and not modes
            release.set()
            assert await asyncio.wait_for(task, 3) == 0
            assert not model.requests
            assert list(app._pending_messages) == ["/plan Review Foo\nKeepCase"]
            assert not app._queue_autosend
            expected = "default" if commit_fails else "plan"
            assert runtime.thread_settings.collaboration_mode == expected
            assert modes == [expected]
            assert "Turn interrupted." in notices
            assert "Plan mode enabled." not in notices
            if not commit_fails:
                assert app._default_collaboration_settings == before
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


def test_plan_command_during_active_turn_is_not_steering(tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        class HeldModel(Model):
            async def stream(self, request):
                entered.set()
                await release.wait()
                async for event in super().stream(request):
                    yield event

        class UI:
            submitted = False

            async def read_message(self):
                await entered.wait()
                if not self.submitted:
                    self.submitted = True
                    return "/plan MustNotBecomeSteering"
                await asyncio.Future()

            async def append_assistant_delta_live(self, text):
                pass

            def show_notice(self, text):
                if "/plan is unavailable" in text:
                    release.set()

            def __getattr__(self, name):
                if name.startswith(("show_", "begin_", "end_", "set_")):
                    return lambda *args, **kwargs: None
                raise AttributeError(name)

        model = HeldModel()
        runtime = await make_runtime(tmp_path, model)
        app = CorkiApplication(
            settings(tmp_path), CorkiPaths.from_home(tmp_path / "home"), runtime, UI()
        )
        try:
            await asyncio.wait_for(app._consume_realtime_turn("original"), 5)
            assert len(model.requests) == 1
            assert runtime.thread_settings.collaboration_mode == "default"
            history = await runtime._repository.load_items(runtime.thread_id)
            assert [i.content for i in history if isinstance(i, UserMessageItem)] == ["original"]
        finally:
            release.set()
            await runtime.aclose()

    asyncio.run(scenario())
