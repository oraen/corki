import asyncio

import pytest
from prompt_toolkit import PromptSession
from prompt_toolkit.history import DummyHistory
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from corki.cli.user_input import collect_user_input
from corki.protocol.events import UserInputRequested
from corki.protocol.user_input import UserInputOption, UserInputQuestion


@pytest.mark.parametrize(
    "keys,expected",
    [
        ("\x1b[6~\r\r", [[], ["A"]]),
        ("\x1b[6~\rj\r\r\r", [["A"], ["A"]]),
        ("\x1b[6~\r\x7f\r\r", [["A"], ["A"]]),
        ("\tfirst\r\tsecond\x1b[5~\r\r", [["A", "user_note: first"], ["A", "user_note: second"]]),
    ],
)
def test_multiple_questions_preserve_commits_and_confirm_unanswered(keys, expected):
    async def scenario(pipe):
        session = PromptSession(input=pipe, output=DummyOutput(), history=DummyHistory())
        session.app.ttimeoutlen = 0.01
        ready = asyncio.Event()
        original = session.prompt_async

        async def prompt(*args, **kwargs):
            return await original(*args, **kwargs, pre_run=ready.set)

        session.prompt_async = prompt
        request = UserInputRequested(
            "thread",
            "turn",
            "call",
            tuple(
                UserInputQuestion(str(i), "Scope", "Which scope?", (UserInputOption("A", "Small"),))
                for i in range(2)
            ),
        )
        task = asyncio.create_task(collect_user_input(session, request))
        try:
            await asyncio.wait_for(ready.wait(), 1)
            pipe.send_text(keys)
            assert await asyncio.wait_for(task, 2) == {
                "answers": {str(i): {"answers": answers} for i, answers in enumerate(expected)}
            }
            assert not list(session.history.get_strings())
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    with create_pipe_input() as pipe:
        asyncio.run(scenario(pipe))


@pytest.mark.parametrize(
    "keys,answers",
    [
        ("\r", ["A"]),
        ("\x1b[B\r", ["B"]),
        ("2", ["B"]),
        ("\tcustom\r", ["A", "user_note: custom"]),
        ("\tfirst\x1b[13;2usecond\r", ["A", "user_note: first\nsecond"]),
        ("\x1b[13;2u\r", ["A"]),
        ("\x1b[B\x1b[B\rcustom\r", ["None of the above", "user_note: custom"]),
        ("\x1b", None),
        ("\x03", None),
    ],
)
def test_question_panel_commits_only_on_explicit_input(keys, answers):
    async def scenario(pipe):
        session = PromptSession(input=pipe, output=DummyOutput(), history=DummyHistory())
        session.app.ttimeoutlen = 0.01
        ready = asyncio.Event()
        original = session.prompt_async

        async def prompt(*args, **kwargs):
            return await original(*args, **kwargs, pre_run=ready.set)

        session.prompt_async = prompt
        request = UserInputRequested(
            "thread",
            "turn",
            "call",
            (
                UserInputQuestion(
                    "scope",
                    "Scope",
                    "Which scope?",
                    (
                        UserInputOption("A", "Small"),
                        UserInputOption("B", "Large"),
                    ),
                ),
            ),
        )
        task = asyncio.create_task(collect_user_input(session, request))
        try:
            await asyncio.wait_for(ready.wait(), 1)
            assert not task.done()
            pipe.send_text(keys)
            expected = None if answers is None else {"answers": {"scope": {"answers": answers}}}
            assert await asyncio.wait_for(task, 2) == expected
            assert session.default_buffer.text == ""
            assert list(session.history.get_strings()) == []
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    with create_pipe_input() as pipe:
        asyncio.run(scenario(pipe))


@pytest.mark.parametrize("blocking,touch", [(False, False), (False, True), (True, False)])
def test_only_untouched_nonblocking_questions_auto_resolve_empty(blocking, touch):
    async def scenario(pipe):
        session = PromptSession(input=pipe, output=DummyOutput(), history=DummyHistory())
        ready = asyncio.Event()
        original = session.prompt_async

        async def prompt(*args, **kwargs):
            return await original(*args, **kwargs, pre_run=ready.set)

        session.prompt_async = prompt
        request = UserInputRequested(
            "thread",
            "turn",
            "call",
            (
                UserInputQuestion(
                    "scope", "Scope", "Which scope?", (UserInputOption("A", "Small"),)
                ),
            ),
            is_blocking=blocking,
        )
        now = [0.0]
        task = asyncio.create_task(collect_user_input(session, request, clock=lambda: now[0]))
        try:
            await asyncio.wait_for(ready.wait(), 1)
            if touch:
                pipe.send_text("\t")
                await asyncio.sleep(0.03)
            now[0] = 121.0
            session.app.invalidate()
            if not blocking and not touch:
                assert await asyncio.wait_for(task, 2) == {"answers": {}}
            else:
                await asyncio.sleep(0.03)
                assert not task.done()
                pipe.send_text("\r")
                assert await asyncio.wait_for(task, 2) == {"answers": {"scope": {"answers": ["A"]}}}
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    with create_pipe_input() as pipe:
        asyncio.run(scenario(pipe))
