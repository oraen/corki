"""Incomplete plans never become completed items through failure or display replay."""

import asyncio
from io import StringIO

import pytest
from rich.console import Console

from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelError, ModelTextDelta
from corki.protocol.events import (
    AssistantMessageInterrupted,
    ProposedPlanCompleted,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
)
from corki.protocol.items import AssistantMessageItem, new_step_id


@pytest.mark.parametrize("ending", ["failure", "cancel", "retry"])
@pytest.mark.parametrize("commit_first", [False, True])
def test_incomplete_plan_cleanup_and_retry_preserve_only_committed_display(
    tmp_path, ending, commit_first
):
    async def scenario():
        committed = asyncio.Event()
        observed, requests, closed = [], [], []
        recovered = "<proposed_plan>\n- recovered plan\n</proposed_plan>"

        class Model:
            async def stream(self, request):
                requests.append(request)
                attempt = len(requests)
                try:
                    if attempt == 1:
                        yield ModelTextDelta("<proposed_plan>\n")
                        if commit_first:
                            yield ModelTextDelta("- committed draft\n")
                            await committed.wait()
                        yield ModelTextDelta("- hidden draft\n")
                        if ending == "cancel":
                            raise asyncio.CancelledError
                        raise ModelError("fixture incomplete plan", retryable=ending == "retry")
                    assert attempt == 2 and ending == "retry"
                    assert not any(isinstance(i, AssistantMessageItem) for i in request.items)
                    yield ModelCompleted(
                        (AssistantMessageItem(recovered, request.items[-1].turn_id, new_step_id()),)
                    )
                finally:
                    closed.append(attempt)

            async def aclose(self):
                pass

        class UI(TerminalUI):
            hold = False

            def begin_proposed_plan(self, item_id=None):
                self.hold = False
                super().begin_proposed_plan(item_id)

            async def append_proposed_plan_delta_live(self, delta):
                if "hidden draft" in delta:
                    self.hold = True
                await super().append_proposed_plan_delta_live(delta)

            async def commit_stream_tick(self, *, catch_up_only=False, finish=False):
                # Hold provisional commits deterministically, but allow final
                # drains so incorrect cleanup ordering would expose hidden rows.
                if self.hold and not finish:
                    return
                await super().commit_stream_tick(catch_up_only=catch_up_only, finish=finish)
                stream = getattr(self, "_plan_stream", None)
                if stream is not None and stream.emitted:
                    committed.set()

        configured = CorkiSettings(
            tmp_path,
            skills_enabled=False,
            plugins_enabled=False,
            collaboration_mode="plan",
            model_max_retries=1,
            model_retry_base_seconds=0.001,
        )
        runtime = await LangGraphRuntime.acreate(
            settings=configured,
            model=Model(),
            database_path=tmp_path / "s.db",
            home_path=tmp_path / "home",
        )
        output = StringIO()
        ui = UI(
            configured,
            tmp_path / "history",
            console=Console(file=output, force_terminal=True, width=40),
        )
        app = CorkiApplication(configured, CorkiPaths.from_home(tmp_path), runtime, ui)

        async def events():
            async for event in runtime.stream("Plan work"):
                observed.append(event)
                yield event

        try:
            if ending == "cancel":
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(app._consume_events(events()), 5)
            else:
                await asyncio.wait_for(app._consume_events(events()), 5)
            terminal = observed[-1]
            expected = {"failure": TurnFailed, "cancel": TurnCancelled, "retry": TurnCompleted}[
                ending
            ]
            assert isinstance(terminal, expected)
            assert (
                sum(isinstance(e, (TurnFailed, TurnCancelled, TurnCompleted)) for e in observed)
                == 1
            )
            plans = [e.text for e in observed if isinstance(e, ProposedPlanCompleted)]
            assert plans == (["- recovered plan\n"] if ending == "retry" else [])
            assert sum(isinstance(e, AssistantMessageInterrupted) for e in observed) == (
                ending == "retry"
            )
            assert closed == ([1, 2] if ending == "retry" else [1])
            assert (
                await runtime._repository.load_model_step(terminal.thread_id, terminal.turn_id, 0)
                is None
            )
            assert (
                await runtime._repository.load_partial_step(terminal.thread_id, terminal.turn_id, 0)
                == ()
            )
            history = await runtime._repository.load_items(runtime.thread_id)
            assert [i.content for i in history if isinstance(i, AssistantMessageItem)] == (
                [recovered] if ending == "retry" else []
            )
            assert "hidden draft" not in output.getvalue()
            for width in (40, 100):
                replay = ui._transcript.render(width)
                assert "hidden draft" not in replay
                assert ("committed draft" in replay) is commit_first
                assert ("recovered plan" in replay) is (ending == "retry")
            assert ui._plan_stream is None and not ui._animation_enabled
            assert not any(
                t.get_name() in {"corki-display-next-event", "corki-stream-commit"}
                for t in asyncio.all_tasks()
            )
        finally:
            committed.set()
            await runtime.aclose()

    asyncio.run(scenario())
