import asyncio

import pytest
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from corki.cli.backtrack import editable_prompts
from corki.cli.backtrack_pages import BacktrackPages
from corki.cli.backtrack_picker import choose_prompt
from corki.protocol.items import AssistantMessageItem, ToolResultItem, UserMessageItem


def history():
    return (
        UserMessageItem("first 中文\nsecond line", "turn1"),
        AssistantMessageItem("answer one", "turn1", "step1"),
        ToolResultItem("call", "exec_command", "output\n" + "x" * 5000, "turn1"),
        UserMessageItem("latest", "turn2"),
        AssistantMessageItem("answer two", "turn2", "step2"),
    )


def test_selection_offsets_keep_context_and_ignore_retained_copies():
    items = history()
    items += (UserMessageItem("retained", "turn3", retained_from_id=items[0].id),)
    prompts = editable_prompts(items)
    pages = BacktrackPages(items, prompts)
    text, targets, _ = pages.page((0, 0))
    assert len(pages.prompts) == 2
    assert text[slice(*targets[pages.prompts[0]])] == "› first 中文\nsecond line\n\n"
    assert text[slice(*targets[pages.prompts[1]])] == "› latest\n\n"
    assert "answer one" in text and "answer two" in text and "x" * 5000 in text
    assert "retained" not in text
    with pytest.raises(ValueError, match="match visible history"):
        BacktrackPages(items, (UserMessageItem("missing", "turn"),))


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        ("\r", 1),
        ("\x1b\r", 0),
        ("\x1b[D\x1b[D\r", 0),
        ("\x1b[D\x1b[C\x1b[C\r", 1),
        ("q", None),
        ("\x14", None),
        ("\x03", None),
    ],
    ids=["latest", "escape-older", "left-boundary", "right-boundary", "q", "ctrl-t", "ctrl-c"],
)
def test_prompt_navigation_and_cancel(keys, expected):
    async def scenario(pipe):
        items = history()
        task = asyncio.create_task(
            choose_prompt(items, editable_prompts(items), input=pipe, output=DummyOutput())
        )
        try:
            pipe.send_text(keys)
            async with asyncio.timeout(5):
                assert await task == expected
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    with create_pipe_input() as pipe:
        asyncio.run(scenario(pipe))


@pytest.mark.parametrize(
    "keys,expected",
    [
        ("\x1b[D\r", 0),
        ("\x1b[D\x1b[F\x1b[6~\x1b[H\x1b[5~\r", 0),
        ("\x1b[D\x1b[F\x1b[6~q", None),
    ],
)
def test_large_history_still_allows_selecting_original_prompt(keys, expected):
    async def scenario(pipe):
        items = (
            UserMessageItem("first", "turn1"),
            ToolResultItem("call", "exec_command", "x" * 8_000_001, "turn1"),
            UserMessageItem("second", "turn2"),
        )
        task = asyncio.create_task(
            choose_prompt(items, editable_prompts(items), input=pipe, output=DummyOutput())
        )
        try:
            pipe.send_text(keys)
            async with asyncio.timeout(5):
                assert await task == expected
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    with create_pipe_input() as pipe:
        asyncio.run(scenario(pipe))
