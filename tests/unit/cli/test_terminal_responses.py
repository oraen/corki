import asyncio

import pytest
from prompt_toolkit import PromptSession
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.input.vt100_parser import Vt100Parser
from prompt_toolkit.keys import Keys
from prompt_toolkit.output import DummyOutput

from corki.cli.approval import choose_approval
from corki.cli.terminal_responses import TerminalResponseParser, frame_terminal_responses


@pytest.mark.parametrize("chunk_size", [1, 2, 7, 4096])
@pytest.mark.parametrize("ending", ["\x07", "\x1b\\"])
def test_fragmented_replies_preserve_keys_paste_and_cpr(chunk_size, ending):
    events = []
    parser = TerminalResponseParser(Vt100Parser(events.append))
    data = (
        "draft界\x1b]10;rgb:f/80/ffff"
        + ending
        + "\x1b[200~hello\x1b]11;rgba:ffff/ffff/ffff/f"
        + ending
        + " world\x1b]52;literal\x07\x1b[201~\x1b[12;34R\x1b[A"
    )
    for offset in range(0, len(data), chunk_size):
        parser.feed(data[offset : offset + chunk_size])
    parser.flush()
    assert parser.colors == {10: (255, 128, 255), 11: (255, 255, 255)}
    assert [(event.key, event.data) for event in events] == [
        *((char, char) for char in "draft界"),
        (Keys.BracketedPaste, "hello world\x1b]52;literal\x07"),
        (Keys.CPRResponse, "\x1b[12;34R"),
        (Keys.Up, "\x1b[A"),
    ]


@pytest.mark.parametrize("paste", [False, True])
def test_oversized_reply_is_bounded_but_pasted_text_is_preserved(paste):
    events = []
    parser = TerminalResponseParser(Vt100Parser(events.append))
    if paste:
        parser.feed("\x1b[200~")
    text = "\x1b]10;" + "x" * 4096 + "1\r"
    parser.feed(text)
    parser.flush()
    assert len(parser.pending) <= 1024
    assert events == []
    parser.feed("\x07" + ("\x1b[201~" if paste else "") + "tail")
    parser.flush()
    if paste:
        assert (events[0].key, events[0].data) == (Keys.BracketedPaste, text + "\x07")
        events.pop(0)
    assert "".join(event.data for event in events) == "tail"
    assert parser.colors == {}


def test_incomplete_reply_preserves_paste_end_and_explicit_cancel():
    events = []
    parser = TerminalResponseParser(Vt100Parser(events.append))
    parser.feed("\x1b[200~\x1b]10;unfinished\x1b[201~")
    assert [(event.key, event.data) for event in events] == [
        (Keys.BracketedPaste, "\x1b]10;unfinished")
    ]
    events.clear()
    parser.feed("\x1b]11;partial")
    parser.flush()
    assert events == []
    parser.feed("\x03")
    parser.feed("\x1b")
    parser.flush()
    assert [event.key for event in events] == [Keys.ControlC, Keys.Escape]


def test_installation_keeps_one_parser_and_does_not_touch_host_input():
    assert frame_terminal_responses(object()) is None
    with create_pipe_input() as pipe:
        reader = pipe.stdin_reader
        parser = frame_terminal_responses(pipe)
        assert parser is frame_terminal_responses(pipe)
        assert pipe.stdin_reader is reader


@pytest.mark.parametrize("ending", ["\x07", "\x1b\\"])
@pytest.mark.parametrize(
    "decision,expected", [("1", "accept"), ("2", "decline"), ("\x03", "cancel")]
)
def test_color_reply_cannot_decide_approval(ending, decision, expected):
    async def scenario(pipe):
        session = PromptSession(input=pipe, output=DummyOutput())
        session.app.ttimeoutlen = 0.01
        ready = asyncio.Event()
        session.app.before_render += lambda _: ready.set()
        task = asyncio.create_task(choose_approval(session, execution=True))
        try:
            await asyncio.wait_for(ready.wait(), 1)
            pipe.send_text("\x1b]11;rgb:ffff/ffff/ffff" + ending)
            await asyncio.sleep(0.05)
            assert not task.done(), "terminal response is not an approval decision"
            assert session.default_buffer.text == ""
            pipe.send_text(decision)
            assert await asyncio.wait_for(task, 1) == expected
            assert list(session.history.get_strings()) == []
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    with create_pipe_input() as pipe:
        asyncio.run(scenario(pipe))
