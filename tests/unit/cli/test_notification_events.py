import asyncio
from types import SimpleNamespace

import pytest

from corki.cli.application import CorkiApplication, _DisplayStreams
from corki.cli.notifications import TerminalNotifications
from corki.config import CorkiSettings
from corki.protocol.events import AssistantMessageCompleted, TurnCompleted, TurnFailed


@pytest.mark.parametrize(
    "replayed,new_answer,expected", [(False, False, 1), (True, False, 0), (True, True, 1)]
)
def test_runtime_completion_notification_respects_replay(tmp_path, replayed, new_answer, expected):
    async def scenario():
        output = []
        notices = TerminalNotifications(
            SimpleNamespace(write_raw=output.append, flush=lambda: None),
            CorkiSettings(tmp_path, notification_condition="always", notification_method="bel"),
            interactive=True,
            environ={},
        )
        app = CorkiApplication.__new__(CorkiApplication)
        app._ui = SimpleNamespace(
            notify=notices.notify, show_assistant_message=lambda *a, **k: None
        )
        app._replayed_answer_turns = {"turn"} if replayed else set()

        async def events():
            if new_answer:
                yield AssistantMessageCompleted("thread", "turn", "new answer")
            yield TurnCompleted("thread", "turn", "")
            yield TurnCompleted("thread", "turn", "")

        await app._render_events_owned(events(), _DisplayStreams())
        await asyncio.sleep(0)
        assert output == ["\a"] * expected

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["shell_approval", "patch_approval", "form"])
def test_approval_routes_notification_and_still_delivers_decision(tmp_path, kind):
    async def scenario():
        notifications, decisions = [], []

        async def elicit(request):
            return "accept", {}

        app = CorkiApplication.__new__(CorkiApplication)
        app._ui = SimpleNamespace(notify=lambda *args: notifications.append(args))
        app._input = SimpleNamespace(elicit=elicit)
        app._runtime = SimpleNamespace(
            respond_execution_approval=lambda *args, **kwargs: decisions.append((args, kwargs)),
            respond_mcp_elicitation=lambda *args, **kwargs: decisions.append((args, kwargs)),
        )
        await app._handle_elicitation(
            SimpleNamespace(kind=kind, request_id="r", server_name="local")
        )
        assert notifications == [("approval-requested", ("local", "r"))]
        assert len(decisions) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("blocking,kind", [(True, "plan-mode-prompt"), (False, "async-question")])
def test_question_response_survives_notification_failure(blocking, kind):
    async def scenario():
        decisions = []
        attempted = []

        def broken(*args):
            attempted.append(args)
            raise OSError("terminal closed")

        async def ask(request):
            return {"answer": "selected"}

        app = CorkiApplication.__new__(CorkiApplication)
        app._ui = SimpleNamespace(notify=broken)
        app._input = SimpleNamespace(ask_user=ask)
        app._runtime = SimpleNamespace(respond_user_input=lambda *args: decisions.append(args))
        await app._handle_user_input(SimpleNamespace(turn_id="turn", call_id="call", is_blocking=blocking))
        assert decisions == [("turn", "call", {"answer": "selected"})]
        assert attempted == [(kind, ("turn", "call"))]

    asyncio.run(scenario())


def test_runtime_failure_notifies_once_without_exposing_error_details(tmp_path):
    async def scenario():
        output, messages = [], []
        notices = TerminalNotifications(
            SimpleNamespace(write_raw=output.append, flush=lambda: None),
            CorkiSettings(tmp_path, notification_condition="always", notification_method="osc9"),
            interactive=True, environ={},
        )
        app = CorkiApplication.__new__(CorkiApplication)
        app._ui = SimpleNamespace(notify=notices.notify, show_assistant_message=lambda *a, **k: messages.append(a))
        app._display_model_error = lambda message: message
        async def events():
            yield TurnFailed("thread", "turn", "PRIVATE_FAILURE")
            yield TurnFailed("thread", "turn", "PRIVATE_FAILURE")
        await app._render_events_owned(events(), _DisplayStreams())
        await asyncio.sleep(0)
        assert output == ["\x1b]9;Corki task failed\a"]
        assert messages
    asyncio.run(scenario())
