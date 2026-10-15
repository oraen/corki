import asyncio
import io
from types import SimpleNamespace

import pytest
from rich.console import Console

from corki.cli.application import CorkiApplication
from corki.cli.backtrack import editable_prompts, prompt_input
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.input_mentions import InputMention
from corki.protocol.items import (
    AssistantMessageItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ImageAttachment, ToolCall, ToolResult, ToolSpec
from corki.sessions.models import DisplayHistory, DisplayTurn, TurnStatus
from corki.tools import ToolRegistry


def test_prompt_edit_forks_without_resampling_original_history(tmp_path):
    async def scenario():
        calls = []
        settings = CorkiSettings(tmp_path, skills_enabled=False, plugins_enabled=False)
        executed = []
        workspace_file = tmp_path / "tool-created.txt"

        class Tool:
            spec = ToolSpec("write_fixture", "local fixture", {"type": "object"})

            async def execute(self, call, context):
                executed.append(call.id)
                workspace_file.write_text("must remain unchanged", encoding="utf-8")
                return ToolResult(call.id, call.name, "fixture written")

        class Model:
            async def stream(self, request):
                calls.append(request)
                if len(calls) == 1:
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall("fixture-call", "write_fixture", {}),
                                request.items[-1].turn_id,
                                new_step_id(),
                            ),
                        )
                    )
                    return
                yield ModelCompleted(
                    (AssistantMessageItem("answer", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        async def create(**kwargs):
            registry = ToolRegistry()
            registry.register(Tool())
            return await LangGraphRuntime.acreate(
                settings=settings,
                model=Model(),
                registry=registry,
                database_path=tmp_path / "state.db",
                home_path=tmp_path / "home",
                **kwargs,
            )

        source = await create()
        target = None
        try:
            for text in ("first", "second"):
                async for _ in source.stream(text):
                    pass
            original = await source.load_display_snapshot()
            assert any(isinstance(item, ToolResultItem) for item in original.items)
            ui = TerminalUI(settings, tmp_path / "history", console=Console(file=io.StringIO()))
            app = CorkiApplication(settings, CorkiPaths.from_home(tmp_path / "home"), source, ui)

            async def choose(read, prompts):
                assert [p.content for p in prompts] == ["first", "second"]
                return 1

            app._input._modal = choose

            async def branch(source_id, index, prompt):
                nonlocal target
                target = await create(fork_from_thread_id=source_id, fork_before_user_message=index)
                await target.load_display_snapshot()
                branch_ui = TerminalUI(
                    settings, tmp_path / "history2", console=Console(file=io.StringIO())
                )
                branch_ui.restore_queued_inputs((prompt_input(prompt),))
                return CorkiApplication(
                    settings, CorkiPaths.from_home(tmp_path / "home"), target, branch_ui
                )

            app._branch_factory = branch
            assert await app._edit_previous_prompt()
            assert app.next_application._ui._draft == "second"
            assert target.thread_id != source.thread_id
            assert [p.content for p in editable_prompts(await target.load_display_snapshot())] == [
                "first"
            ]
            assert len(calls) == 3 and executed == ["fixture-call"]
            assert [event async for event in target.resume_pending()] == []
            assert len(calls) == 3 and executed == ["fixture-call"]
            assert workspace_file.read_text(encoding="utf-8") == "must remain unchanged"
            assert await source.load_display_snapshot() == original
        finally:
            if target is not None:
                await target.aclose()
            await source.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "outcome", ["failure", "cancel", "changed", "dismiss", "load_failure", "stale", "running"]
)
def test_failed_branch_restores_only_selected_source_draft(tmp_path, outcome):
    async def scenario():
        settings = CorkiSettings(tmp_path, skills_enabled=False, plugins_enabled=False)
        selector = InputMention("review", "/local/SKILL.md", "skill")
        image = ImageAttachment("data:image/png;base64,test")
        prompt = UserMessageItem(
            "中文 [Image #1] $review\n继续",
            "turn",
            attachments=(image,),
            image_positions=(3,),
            mentions=(selector,),
        )
        calls = []
        loads = 0

        async def snapshot():
            nonlocal loads
            loads += 1
            if outcome == "load_failure":
                raise OSError("private path must not be displayed")
            if loads > 1 and outcome == "stale":
                return DisplayHistory((), ())
            if outcome == "running":
                return DisplayHistory((prompt,), (DisplayTurn("turn", TurnStatus.RUNNING),))
            return DisplayHistory((prompt,), (DisplayTurn("turn", TurnStatus.COMPLETED),))

        runtime = SimpleNamespace(thread_id="source", load_display_snapshot=snapshot)
        output = io.StringIO()
        ui = TerminalUI(settings, tmp_path / "history", console=Console(file=output))
        app = CorkiApplication(settings, CorkiPaths.from_home(tmp_path / "home"), runtime, ui)

        async def choose(read, prompts):
            return None if outcome == "dismiss" else 0

        async def branch(source, index, selected):
            calls.append((source, index, selected))
            if outcome == "cancel":
                raise asyncio.CancelledError
            if outcome == "changed":
                runtime.thread_id = "another"
            raise OSError("private path must not be displayed")

        app._input._modal = choose
        app._branch_factory = branch
        if outcome == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await app._edit_previous_prompt()
        else:
            assert not await app._edit_previous_prompt()
        assert app.next_application is None
        assert calls == (
            []
            if outcome in {"dismiss", "load_failure", "stale", "running"}
            else [("source", 0, prompt)]
        )
        if outcome in {"failure", "stale", "running"}:
            assert ui._draft == prompt.content
            assert ui._inline_images.images == (image,)
            assert ui._inline_images.positions == (3,)
            assert ui._inline_images.mentions == (selector,)
        else:
            assert not ui._draft
            assert not ui._inline_images.elements
        assert "private path" not in output.getvalue()
        assert not (tmp_path / "history").exists()

    asyncio.run(scenario())
