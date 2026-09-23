"""Plan markup is a local output projection, never a provider extension."""

import asyncio
from io import StringIO

import pytest
from rich.console import Console
from test_thread_settings_update import make_runtime, settings

from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths
from corki.models import ModelCompleted, ModelItemCompleted, ModelTextDelta
from corki.protocol.events import AssistantMessageCompleted, AssistantTextDelta, TurnCompleted
from corki.protocol.items import AssistantMessageItem, new_step_id


@pytest.mark.parametrize("mode", ["default", "plan"])
@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("shape", ["mixed", "only_plan", "multiple_blocks"])
def test_plan_output_separates_events_without_rewriting_history(tmp_path, mode, streamed, shape):
    async def scenario():
        text = "Intro\n<proposed_plan>\n- first\n</proposed_plan>\nOutro"
        normal = "Intro\nOutro"
        if shape != "mixed":
            text, normal = "<proposed_plan>\n- first\n</proposed_plan>\n", ""
            if shape == "multiple_blocks":
                text = "<proposed_plan>\nold draft\n</proposed_plan>\n" + text

        class Model:
            async def stream(self, request):
                item = AssistantMessageItem(text, request.items[-1].turn_id, new_step_id())
                if streamed:
                    for character in text:
                        yield ModelTextDelta(character, item.id)
                    yield ModelItemCompleted(item)
                yield ModelCompleted((item,))

            async def aclose(self):
                pass

        runtime = await make_runtime(
            tmp_path, Model(), configured=settings(tmp_path, collaboration_mode=mode)
        )
        try:
            events = [event async for event in runtime.stream("plan it")]
            assert isinstance(events[-1], TurnCompleted)
            messages = [
                event.text for event in events if isinstance(event, AssistantMessageCompleted)
            ]
            visible = "".join(
                event.delta for event in events if isinstance(event, AssistantTextDelta)
            )
            plans = [event for event in events if type(event).__name__ == "ProposedPlanCompleted"]
            if mode == "plan":
                assert messages == ([normal] if normal else [])
                assert visible == normal
                assert [event.text for event in plans] == ["- first\n"]
            else:
                assert messages == [text]
                assert visible == text
                assert plans == []
            history = await runtime._repository.load_items(runtime.thread_id)
            assert [item.content for item in history if isinstance(item, AssistantMessageItem)] == [
                text
            ]
            if mode == "plan":
                configured = settings(tmp_path, collaboration_mode="default")
                rendered = StringIO()
                ui = TerminalUI(
                    configured, tmp_path / "ui-history", console=Console(file=rendered, width=100)
                )
                app = CorkiApplication(configured, CorkiPaths.from_home(tmp_path), runtime, ui)

                async def playback_events():
                    for event in events:
                        yield event

                await app._render_events(playback_events())
                assert rendered.getvalue().count("Proposed Plan") == 1
                assert "first" in rendered.getvalue()
                assert "<proposed_plan>" not in rendered.getvalue()
                replayed = StringIO()
                replay_ui = TerminalUI(
                    configured,
                    tmp_path / "replay-history",
                    console=Console(file=replayed, width=100),
                )
                replay_ui.replay_history(await runtime.load_display_snapshot())
                assert replayed.getvalue().count("Proposed Plan") == 1
                assert "first" in replayed.getvalue()
                assert "<proposed_plan>" not in replayed.getvalue()
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
