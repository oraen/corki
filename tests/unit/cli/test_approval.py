import asyncio

import pytest
from prompt_toolkit import PromptSession
from prompt_toolkit.history import DummyHistory
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from corki.cli.approval import choose_approval


@pytest.mark.parametrize(
    "keys,expected",
    [
        ("\r", "accept"),
        ("1", "accept"),
        ("2", "decline"),
        ("3", "cancel"),
        ("\x1b[B\r", "decline"),
        ("\x1b[B\x1b[B\r", "cancel"),
        ("\x1b", "cancel"),
        ("\x03", "cancel"),
        ("\x04", "cancel"),
    ],
)
def test_approval_requires_explicit_key_and_preserves_decision(keys, expected):
    async def scenario(pipe):
        session = PromptSession(input=pipe, output=DummyOutput(), history=DummyHistory())
        session.app.ttimeoutlen = 0.01
        ready = asyncio.Event()
        original = session.prompt_async

        async def prompt(*args, **kwargs):
            return await original(*args, **kwargs, pre_run=ready.set)

        session.prompt_async = prompt
        task = asyncio.create_task(choose_approval(session))
        try:
            await asyncio.wait_for(ready.wait(), 1)
            assert not task.done(), "highlighting a default must not submit consent"
            pipe.send_text(keys)
            assert await asyncio.wait_for(task, 2) == expected
            assert session.default_buffer.text == ""
            assert list(session.history.get_strings()) == []
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    with create_pipe_input() as pipe:
        asyncio.run(scenario(pipe))


@pytest.mark.parametrize(
    "keys,expected",
    [
        ("\r", ("accept", {"scope": "once"})),
        ("1", ("accept", {"scope": "once"})),
        ("2", ("accept", {"scope": "session"})),
        ("3", ("accept", {"scope": "rule"})),
        ("4", ("decline", None)),
        ("5", ("cancel", None)),
        ("\x1b[B\x1b[B1", ("accept", {"scope": "once"})),
        ("y", ("accept", {"scope": "once"})),
        ("\x1b[B\x1b[By", ("accept", {"scope": "once"})),
        ("a", ("accept", {"scope": "session"})),
        ("p", ("accept", {"scope": "rule"})),
        ("d", ("decline", None)),
        ("n", ("cancel", None)),
        ("\x1b[B\r", ("accept", {"scope": "session"})),
        ("\x1b[B\x1b[B\r", ("accept", {"scope": "rule"})),
        ("\x1b[B" * 3 + "\r", ("decline", None)),
        ("\x1b", ("cancel", None)),
        ("\x03", ("cancel", None)),
    ],
)
def test_scoped_execution_choice_requires_explicit_submission(keys, expected):
    async def scenario(pipe):
        session = PromptSession(input=pipe, output=DummyOutput(), history=DummyHistory())
        session.app.ttimeoutlen = 0.01
        ready = asyncio.Event()
        prompt = session.prompt_async

        async def owned_prompt(*args, **kwargs):
            return await prompt(*args, **kwargs, pre_run=ready.set)

        session.prompt_async = owned_prompt
        task = asyncio.create_task(choose_approval(session, scopes=("once", "session", "rule")))
        try:
            await asyncio.wait_for(ready.wait(), 1)
            assert not task.done()
            pipe.send_text(keys)
            assert await asyncio.wait_for(task, 2) == expected
            assert session.default_buffer.text == ""
            assert list(session.history.get_strings()) == []
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    with create_pipe_input() as pipe:
        asyncio.run(scenario(pipe))


def test_paste_and_unavailable_scope_cannot_authorize():
    async def scenario(pipe):
        session = PromptSession(input=pipe, output=DummyOutput(), history=DummyHistory())
        ready = asyncio.Event()
        prompt = session.prompt_async

        async def owned_prompt(*args, **kwargs):
            return await prompt(*args, **kwargs, pre_run=ready.set)

        session.prompt_async = owned_prompt
        task = asyncio.create_task(choose_approval(session, scopes=("once",)))
        try:
            await asyncio.wait_for(ready.wait(), 1)
            pipe.send_text("ap049\x1b[200~1\n2\n3\ny\na\np\n\x1b[201~")
            await asyncio.sleep(0.05)
            assert not task.done()
            assert session.default_buffer.text == ""
            assert list(session.history.get_strings()) == []
            pipe.send_text("\x03")
            assert await asyncio.wait_for(task, 1) == ("cancel", None)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    with create_pipe_input() as pipe:
        asyncio.run(scenario(pipe))
