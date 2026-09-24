from dataclasses import replace

import pytest

from corki.cli.backtrack import BacktrackUnavailable, validate_selection
from corki.protocol.items import UserMessageItem
from corki.sessions.models import DisplayHistory, DisplayTurn, TurnStatus


@pytest.mark.parametrize("status", list(TurnStatus))
def test_only_terminal_turns_can_be_edited(status):
    prompt = UserMessageItem("question", "turn")
    history = DisplayHistory((prompt,), (DisplayTurn("turn", status),))
    if status in {TurnStatus.CREATED, TurnStatus.RUNNING}:
        with pytest.raises(BacktrackUnavailable, match="still in progress"):
            validate_selection(history, 0, prompt)
    else:
        validate_selection(history, 0, prompt)


def test_steer_is_not_an_independent_fork_boundary():
    first = UserMessageItem("initial", "turn")
    steer = UserMessageItem("more instructions", "turn")
    history = DisplayHistory((first, steer), (DisplayTurn("turn", TurnStatus.COMPLETED),))
    validate_selection(history, 0, first)
    with pytest.raises(BacktrackUnavailable, match="steer"):
        validate_selection(history, 1, steer)


def test_stale_and_missing_selection_are_rejected():
    prompt = UserMessageItem("original", "turn")
    history = DisplayHistory((prompt,), (DisplayTurn("turn", TurnStatus.COMPLETED),))
    for index, selected in (
        (1, prompt),
        (-1, prompt),
        (True, prompt),
        (0, replace(prompt, content="changed")),
    ):
        with pytest.raises(BacktrackUnavailable, match="no longer matches"):
            validate_selection(history, index, selected)
    with pytest.raises(BacktrackUnavailable, match="no saved turn status"):
        validate_selection(DisplayHistory((prompt,), ()), 0, prompt)
